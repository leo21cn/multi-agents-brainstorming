import pathlib
import sys

# 保证从 backend/ 目录运行 pytest 时可导入 app 包
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.deps import get_config_service, get_memory_service, get_room_service
from app.main import app
from app.memory.service import MemoryService
from app.persistence.db import get_engine, init_db
from app.rag.vectorstore import InMemoryVectorStore
from app.services.config_service import ConfigService
from app.services.room_service import RoomService
from app.settings import Settings


@pytest.fixture(autouse=True)
def env(tmp_path):
    """每个测试用独立临时 SQLite 与内存向量库，并覆盖服务依赖。"""
    engine = get_engine(Settings("", str(tmp_path / "t.db"), str(tmp_path), 6000, 8000))
    init_db(engine)
    vs = InMemoryVectorStore(str(tmp_path / "vectors.json"))
    config = ConfigService(engine, vs)
    room = RoomService(engine, config)
    memory = MemoryService(engine, 6000, 8000)
    app.dependency_overrides[get_config_service] = lambda: config
    app.dependency_overrides[get_room_service] = lambda: room
    app.dependency_overrides[get_memory_service] = lambda: memory
    yield SimpleNamespace(engine=engine, vs=vs, config=config, room=room, memory=memory)
    app.dependency_overrides.clear()


@pytest.fixture
def client(env):
    return TestClient(app)


@pytest.fixture
def seeded(client):
    conn = client.post(
        "/model-connections",
        json={
            "name": "DashScope 主",
            "provider_type": "OpenAI 兼容",
            "base_url": "http://x",
            "api_key": "sk-xxxxxxxxxxxx3f9a",
            "available_models": ["qwen-max", "qwen-plus"],
        },
    ).json()
    kb = client.post(
        "/knowledge-bases", json={"name": "技术架构", "dimension": "技术架构"}
    ).json()
    card = client.post(
        "/role-cards", json={"name": "架构师", "persona": "资深架构师", "expertise": "技术债"}
    ).json()
    agent = client.post(
        "/agents",
        json={
            "name": "架构师",
            "model_connection_id": conn["conn_id"],
            "model_name": "qwen-max",
            "kb_ids": [kb["kb_id"]],
            "role_card_id": card["card_id"],
        },
    ).json()
    return {"conn": conn, "kb": kb, "card": card, "agent": agent}
