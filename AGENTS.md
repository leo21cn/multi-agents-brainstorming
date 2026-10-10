# AGENTS.md

本文件是 coding agent 在本代码库工作时的主入口。开始任何改动前，先读本文件与 `rules/` 下的相关规则，并查阅 `../docs/` 中的权威文档。

## 1. 项目概述

火花会 Sparkwave（人机混合头脑风暴工具）：人类成员与智能体在同一浏览器群聊房间内围绕一个命题讨论，主持人控制发言节奏，讨论到阶段时生成结构化结论文档。第一版为单机演示系统，绑定软件工程与项目管理交叉领域。

权威文档（冲突时以文档为准，先读再改）：

- `../docs/concept-req.md` 概念需求文档
- `../docs/srs.md` 需求规格说明书（功能需求 FR、验收标准 A）
- `../docs/architecture.md` 技术架构文档（本项目所有技术约定的来源）
- `../docs/user-stories.md` 用户故事（角色、史诗与验收）
- `../docs/data-model.md` 数据模型（实体、枚举、存储映射）
- `../docs/view-spec.md` 用户视图规格说明（视图与数据模型映射）
- `../docs/implementation-plan.md` 实施计划（任务、接口、测试与部署）
- `../docs/testdata/` 测试知识库语料（三维度，含来源说明）

## 2. 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3.11+、FastAPI（REST + WebSocket）、异步 |
| 编排 | AG2（ag2ai/ag2），经适配器接入 |
| 持久化 | SQLite、向量库（Chroma）、本地文件系统 |
| 模型 | 系统级模型连接（OpenAI 兼容端点，默认接 DashScope/Qwen）；智能体选用连接与具体模型 |
| 前端 | React + TypeScript + Vite |

## 3. 仓库结构

```
program/
  AGENTS.md
  rules/                     # 规则文件，见第 5 节
  backend/
    app/
      api/                   # REST 与 WebSocket 接入
      services/              # 配置、房间、发言、结论、来源标注
      orchestration/         # AG2 适配器与调度策略、待发言队列
      rag/                   # 检索、切分、嵌入、向量库
      memory/                # 上下文组装、滚动摘要、记忆服务（L1/L2/L3）
      models/                # 模型提供方接口与 OpenAI 兼容实现
      persistence/           # SQLite、文件系统
      domain/                # 实体与枚举
    tests/                   # unit / integration / e2e / acceptance
  frontend/
    src/
      admin/                 # 管理界面
      room/                  # 房间界面（群聊）
      ws/                    # WebSocket 客户端
  scripts/                   # 语料入库、配置种子、基准跑通
```

相关资产（与本仓库并存，供参照）：

- `../docs/` 需求与设计文档链（见第 1 节权威文档）。
- `../docs/prototype/` 界面原型（房间、管理后台、建房、结论、索引）与 `icon/` 标识。
- `../docs/testdata/` 三维度测试知识库语料（来源与许可见该目录 README）。

## 4. 领域核心不变量（务必遵守）

1. 系统级与房间级分离：模型连接、知识库、智能体、角色卡为系统级实体，跨房间复用；房间仅经选用关系引用智能体，不在建房时配置知识库与智能体本体。
2. 初始化最低要求：系统至少存在一个知识库与一个已绑定知识库的智能体，未达标不允许建房。
3. 智能体配置：选用模型连接与具体模型 + 一个或多个知识库绑定 + 角色提示词卡；未选模型连接、未选模型、未绑定知识库不允许保存；所选模型须在该连接的可用模型名列表内。
4. 知识库三维度固定：业务领域、方法论、技术架构。
5. 编排模式：默认且本版仅完整实现"人类主持控制"；其余三种经调度策略接口预留，不实现内部逻辑。
6. 插嘴：智能体经举手信号入待发言队列，由主持人放行或驳回；举手不打断进行中的发言。
7. 记忆分层 L0–L4，记忆随房间隔离；知识与智能体配置跨房间复用，不属于记忆。
8. 来源标注：结论文档的关键观点与分歧点、导出的每条发言，必须标注来源智能体及其知识库。
9. 凭据：模型连接的 API Key 入库但受保护——界面掩码、仅管理员可见、日志与导出不含、不下发前端明文；环境变量可作为默认值或覆盖。

详见 `rules/02-domain-invariants.md`。

## 5. 规则索引

| 文件 | 内容 |
|------|------|
| `rules/01-architecture-and-layers.md` | 分层边界、依赖方向、扩展点与框架隔离 |
| `rules/02-domain-invariants.md` | 系统级/房间级、初始化、三维知识库、编排、插嘴、来源标注 |
| `rules/03-backend-python.md` | 后端分层、类型与异步、错误处理、日志、配置 |
| `rules/04-frontend-react.md` | 前端两层界面、WebSocket 客户端、状态与展示 |
| `rules/05-memory-and-retrieval.md` | 记忆分层、记忆与知识分离、上下文组装、压缩、来源标注 |
| `rules/06-security-credentials.md` | 凭据、上传校验、访问与导出 |
| `rules/07-testing-and-verification.md` | 测试分层、验收标准、差异化指标、真实模型接入 |

## 6. 常用命令

后端（在 `backend/` 下）：

```
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest
```

前端（在 `frontend/` 下）：

```
npm install
npm run dev
npm run build
npm test
```

环境变量：模型连接的地址与 API Key 在管理界面配置；环境变量可用作默认值或覆盖（如 `DASHSCOPE_API_KEY`）；API Key 不入日志与导出、不下发前端明文。

## 7. 工作方式

- 改代码前先定位并阅读 `../docs/architecture.md` 中的对应章节；接口与行为以文档约定为准。
- 新增能力优先经扩展点接口接入（调度策略、模型提供方、技能、成员身份、会话通道、记忆压缩、长期召回），不修改主流程与既有接口。
- 本版只完整实现默认路径（人类主持控制、滚动摘要、OpenAI 兼容模型接入）；其余扩展点保留接口，不抢先实现内部逻辑。
- 依赖默认接入真实模型（经模型连接配置的 OpenAI 兼容端点，默认 DashScope/Qwen），不以桩实现替代；业务语料可模拟，模型接入不模拟。
- 每次改动运行相关层测试；提交前运行端到端基准命题验证。
- 提交信息说明改动意图；不提交任何含凭据的文件。
