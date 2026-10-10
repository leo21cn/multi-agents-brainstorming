"""建系统级配置：模型连接、三维度知识库、角色卡与 8 个智能体。"""

import os
import sys

from app.domain.enums import KnowledgeDimension
from app.persistence.db import get_engine, init_db
from app.rag.vectorstore import InMemoryVectorStore
from app.services.config_service import ConfigService
from app.settings import Settings

ROSTER = [
    ("业务领域专家", "qwen-plus", ["业务领域"]),
    ("架构师", "qwen-max", ["技术架构"]),
    ("交付负责人", "qwen-plus", ["项目管理与软件工程方法论"]),
    ("一线开发", "qwen-plus", ["技术架构", "项目管理与软件工程方法论"]),
    ("测试负责人", "qwen-turbo", ["项目管理与软件工程方法论"]),
    ("产品负责人", "qwen-plus", ["业务领域", "项目管理与软件工程方法论"]),
    ("成本／风险分析师", "qwen-plus", ["业务领域", "项目管理与软件工程方法论"]),
    ("批判者", "qwen-max", ["业务领域", "项目管理与软件工程方法论", "技术架构"]),
]

AVAILABLE_MODELS = ["qwen-max", "qwen-plus", "qwen-turbo", "text-embedding-v3"]


def seed(db_path, data_dir, api_key: str | None = None) -> dict:
    engine = get_engine(Settings("", str(db_path), str(data_dir), 6000, 8000))
    init_db(engine)
    vs = InMemoryVectorStore(str(data_dir) + "/vectors.json")
    svc = ConfigService(engine, vs)

    svc.create_connection(
        type(
            "C",
            (),
            {
                "name": "DashScope 主",
                "provider_type": "OpenAI 兼容",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": api_key or os.environ.get("DASHSCOPE_API_KEY", ""),
                "available_models": AVAILABLE_MODELS,
            },
        )()
    )
    conn = svc.list_connections()[0]

    kbs = {}
    for dim in KnowledgeDimension:
        kbs[dim.value] = svc.get_or_create_knowledge_base(dim.value, dim)

    cards = {}
    for name, _, _ in ROSTER:
        cards[name] = svc.create_role_card(name, f"{name}的角色人设", f"{name}的专长")

    unbound = 0
    for name, model_name, kb_names in ROSTER:
        agent = svc.create_agent(
            name,
            conn.conn_id,
            model_name,
            [kbs[k].kb_id for k in kb_names],
            cards[name].card_id,
        )
        if not svc.bound_kb_ids(agent.agent_id):
            unbound += 1

    return {
        "connections": len(svc.list_connections()),
        "knowledge_bases": len(svc.list_knowledge_bases()),
        "role_cards": len(svc.list_role_cards()),
        "agents": len(svc.list_agents()),
        "unbound_agents": unbound,
    }


def main() -> None:
    settings = Settings("", "./data/sparkwave.db", "./data", 6000, 8000)
    print(seed(settings.db_path, settings.data_dir))


if __name__ == "__main__":
    sys.exit(main())
