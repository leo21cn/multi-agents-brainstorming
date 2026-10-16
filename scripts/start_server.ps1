# Start the Sparkwave service (project skill: start-server, see skills/start-server/SKILL.md)
#
# Usage:
#   powershell -File scripts/start_server.ps1              # background start (logs to program/server.log)
#   powershell -File scripts/start_server.ps1 -Foreground  # foreground start (most durable; Ctrl+C to stop)
#   powershell -File scripts/start_server.ps1 -Port 8001   # alternate port
#
# ASCII-only messages on purpose: Windows PowerShell 5.1 reads BOM-less .ps1
# files with the ANSI codepage, so non-ASCII literals can break parsing.

param(
    [int]$Port = 8000,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot   # program/
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Error "backend\.venv missing: install dependencies first (see README quick start)."
    exit 1
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Port $Port is already serving (pid $($existing.OwningProcess)); nothing to do."
    exit 0
}

if ($Foreground) {
    Set-Location (Join-Path $root 'backend')
    & $py -m uvicorn app.main:app --port $Port
    exit $LASTEXITCODE
}

$out = Join-Path $root 'server.log'
$err = Join-Path $root 'server.err.log'
$proc = Start-Process -FilePath $py `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--port', "$Port" `
    -WorkingDirectory (Join-Path $root 'backend') `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $err

$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $h = Invoke-RestMethod "http://127.0.0.1:$Port/healthz" -TimeoutSec 2
        if ($h.status -eq 'ok') { $ok = $true; break }
    } catch {}
}

if ($ok) {
    Write-Host "Sparkwave started: http://localhost:$Port/ (pid $($proc.Id), logs: $out / $err)"
} else {
    Write-Host 'Startup failed. Recent logs:'
    Get-Content $out -Tail 10 -ErrorAction SilentlyContinue
    Get-Content $err -Tail 10 -ErrorAction SilentlyContinue
    exit 1
}
