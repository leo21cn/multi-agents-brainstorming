# 火花会 Sparkwave（程序）

人机混合头脑风暴工具的第一版单机演示系统：人类成员与智能体在同一房间内围绕命题讨论，主持人控制发言节奏与放行插嘴，讨论到阶段时生成结构化结论文档并导出完整发言记录。

- 权威文档：`../docs/`（概念需求、SRS、架构、用户故事、数据模型、视图规格、实施计划、测试语料）。
- 协作约定：`AGENTS.md` 与 `rules/`。

## 技术栈

Python 3.11+、FastAPI（REST + WebSocket）、SQLModel（SQLite）、向量库（默认内置 InMemory，可选 Chroma）、AG2（经适配器隔离）、React/静态前端（本仓库以免构建静态页打通闭环）。

## 目录

```
program/
  backend/        # 后端：app/（api, services, orchestration, rag, memory, models, persistence, domain）、tests/
  frontend/       # 演示台静态页（由后端托管于 /app）
  scripts/        # 语料入库、配置种子、基准跑通
  Makefile
```

## 快速开始

```
# 1) 安装依赖
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# 或 source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux

# 2) 入库测试语料（读取 ../docs/testdata）并建系统级配置
cd ..
make seed

# 3) 启动
make up            # http://localhost:8000/healthz ，演示台 http://localhost:8000/app

# 4) 测试与基准
make test
make benchmark
```

> 本机若无 `make`，用等价的跨平台入口：
> `python tasks.py seed` / `python tasks.py up` / `python tasks.py test` / `python tasks.py benchmark`

## 模型接入

- 系统级「模型连接」承载 API 地址、API Key 与可用模型名，统一走 OpenAI 兼容端点。
- 配置了 API Key 时使用真实模型（默认 DashScope/Qwen 兼容端点）；未配置时使用**离线演示提供方**（确定性输出），用于本地跑通闭环。设置 `DASHSCOPE_API_KEY` 即切换为真实模型。
- API Key 入库但受保护：出参一律掩码，测试断言明文绝不回传。

## 接口概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 健康检查 |
| POST/GET/PATCH | `/model-connections` | 模型连接（出参掩码） |
| POST/GET | `/knowledge-bases`、`/documents` | 知识库与文档入库 |
| POST/GET | `/role-cards`、`/agents` | 角色卡与智能体（校验模型/知识库） |
| GET | `/system/init-status` | 初始化状态（建房门槛） |
| POST/GET | `/rooms`、`/rooms/{id}` | 房间与成员 |
| POST | `/rooms/{id}/speak`、`/messages` | 点名发言、人类发言 |
| POST/GET | `/rooms/{id}/raise-hand…` | 举手、放行、驳回 |
| POST | `/rooms/{id}/conclusion`、`/export` | 生成结论、导出记录 |
| WS | `/rooms/{id}/ws` | 房间事件（message/raise_hand/room_state/speech_failed） |

## 验收

`make test` 覆盖功能验收 A-1..A-9 与差异化指标；`make benchmark` 跑通基准命题并输出来源一致率、标注完整率、越权发言数与导出一致性。
