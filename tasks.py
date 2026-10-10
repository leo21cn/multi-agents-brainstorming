"""跨平台任务入口（在 program/ 下运行）；本机无 make 时替代 Makefile。

用法：
  python tasks.py seed
  python tasks.py up
  python tasks.py test
  python tasks.py benchmark
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _python() -> str:
    for cand in ("backend/.venv/Scripts/python.exe", "backend/.venv/bin/python"):
        p = ROOT / cand
        if p.exists():
            return str(p)
    return sys.executable


PY = _python()


def _run(args, cwd=None) -> int:
    return subprocess.call(args, cwd=str(cwd or ROOT))


def seed() -> int:
    code = "import sys; sys.path[:0]=['.','backend']; from scripts.%s import main as m; m()"
    rc = _run([PY, "-c", code % "seed_config"])
    return rc or _run([PY, "-c", code % "seed_corpus"])


def up() -> int:
    return _run([PY, "-m", "uvicorn", "app.main:app", "--port", "8000"], cwd=ROOT / "backend")


def test() -> int:
    return _run([PY, "-m", "pytest", "-q"], cwd=ROOT / "backend")


def benchmark() -> int:
    code = (
        "import sys; sys.path[:0]=['.','backend']; "
        "from scripts.run_benchmark import run; "
        "r=run('scripts/benchmark_propositions.yaml'); "
        "[print(f'{k}: {v}') for k,v in r.items()]"
    )
    return _run([PY, "-c", code])


TASKS = {"seed": seed, "up": up, "test": test, "benchmark": benchmark}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=sorted(TASKS))
    sys.exit(TASKS[parser.parse_args().task]())
