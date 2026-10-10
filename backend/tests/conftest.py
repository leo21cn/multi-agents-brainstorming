import pathlib
import sys

# backend/（导入 app）与 program/（导入 scripts）
_ROOT = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_ROOT.parents[1]))
sys.path.insert(0, str(_ROOT.parents[2]))

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.deps import (
    get_config_service,
    get_conclusion_service,
    get_memory_service,
    get_raise_hand_queue,
    get_room_service,
    get_speech_service,
)
from app.main import app
from app.memory.service import MemoryService
from app.models.demo_provider import DemoProvider
from app.orchestration.queue import RaiseHandQueue
from app.persistence.db import get_engine, init_db
from app.rag.embeddings import EmbeddingAdapter
from app.rag.retrieval import RetrievalService
from app.rag.vectorstore import InMemoryVectorStore
from app.services.conclusion_service import ConclusionService
from app.services.config_service import ConfigService
from app.services.room_service import RoomService
from app.services.speech_service import SpeechService
from app.settings import Settings


@pytest.fixture(autouse=True)
def env(tmp_path):
    """每个测试用独立临时 SQLite、内存向量库与队列，并覆盖服务依赖。"""
    engine = get_engine(Settings("", str(tmp_path / "t.db"), str(tmp_path), 6000, 8000))
    init_db(engine)
    vs = InMemoryVectorStore(str(tmp_path / "vectors.json"))
    config = ConfigService(engine, vs)
    room = RoomService(engine, config)
    memory = MemoryService(engine, 6000, 8000)
    speech = SpeechService(
        engine, config, memory, RetrievalService(vs, EmbeddingAdapter()), lambda a: DemoProvider()
    )
    conclusion = ConclusionService(engine, config, memory, str(tmp_path))
    queue = RaiseHandQueue()

    app.dependency_overrides[get_config_service] = lambda: config
    app.dependency_overrides[get_room_service] = lambda: room
    app.dependency_overrides[get_memory_service] = lambda: memory
    app.dependency_overrides[get_speech_service] = lambda: speech
    app.dependency_overrides[get_conclusion_service] = lambda: conclusion
    app.dependency_overrides[get_raise_hand_queue] = lambda: queue
    yield SimpleNamespace(
        engine=engine,
        vs=vs,
        config=config,
        room=room,
        memory=memory,
        speech=speech,
        conclusion=conclusion,
        queue=queue,
        data_dir=str(tmp_path),
    )
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
            "available_models": ["qwen-max", "qwen-plus", "qwen-turbo"],
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
    client.post(
        "/documents",
        data={"kb_id": kb["kb_id"]},
        files={"file": ("kb.md", "技术债 延期 砍范围 加人 合规 排期 耦合", "text/markdown")},
    )
    return {"conn": conn, "kb": kb, "card": card, "agent": agent}


@pytest.fixture
def make_room(client):
    def _make(agent_id, host="陈"):
        return client.post(
            "/rooms",
            json={
                "proposition": "某功能预计延期两周，应砍范围、加人还是推迟发布？",
                "orchestration_mode": "人类主持控制",
                "agent_ids": [agent_id],
                "host_name": host,
            },
        ).json()

    return _make
