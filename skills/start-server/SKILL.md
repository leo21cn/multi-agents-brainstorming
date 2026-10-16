# 技能：start-server（启动 Sparkwave 服务）

## 用途

当用户说「启动服务」「重启服务」、报告 `localhost:8000` 访问不到，或健康检查失败时，按本技能把服务跑起来。服务即选项「自己在终端运行 `python tasks.py up`」的等价物，本技能把它固化为可复用脚本。

## 何时使用

- 用户要求启动/重启 Sparkwave 服务；
- 探测到 `GET http://127.0.0.1:8000/healthz` 失败或端口 8000 无监听；
- 会话环境回收了服务进程（本环境的已知行为：agent 启动的进程会被定期回收），需要重新拉起。

## 执行步骤

1. 检查现状：`Get-NetTCPConnection -LocalPort 8000 -State Listen`；已有监听则报告即可，不重复启动。
2. 启动（Windows，在 `program/` 下）：
   - 后台启动（agent 代为拉起时用）：`powershell -File scripts/start_server.ps1`——脚本把标准输出与错误写入 `program/server.log` 与 `program/server.err.log`，并轮询健康检查后报告结果。
   - 前台启动（用户自己在终端里跑，进程归用户管理、最持久）：`powershell -File scripts/start_server.ps1 -Foreground`，Ctrl+C 停止。等价命令为 `python tasks.py up`。
3. 验证：`http://127.0.0.1:8000/healthz` 返回 `{"status":"ok"}`；再确认演示台 `/app`、管理后台 `/admin/`、房间页 `/room/` 可打开。
4. 失败排查：读 `program/server.err.log` 与 `program/server.log` 尾部。常见原因：
   - 端口占用 → 用 `-Port 8001` 起备用端口（`tasks.py up` 固定 8000）；
   - `backend\.venv` 缺失 → 按 `program/README.md` 快速开始先装依赖；
   - 模型调用连接错误 → 服务继承启动时 shell 的代理环境，需在代理状态确定的终端里启动（见 `docs/local-run-guide.md` 第 10 节）。

## 注意

- 本会话（DeepSeek Harness）会定期回收 agent 启动的后台进程；检测到服务不在时按本技能重新拉起，并在回复中告知用户。
- 数据库与向量库在 `program/data/`，重启不丢数据；待发言队列、发言权、结论生成门闩为内存态，重启即清空，属预期行为。
- 播种（`python tasks.py seed`）只面向空库、不幂等，服务重启无需重新播种。
