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
    get_conclusion_gate,
    get_config_service,
    get_conclusion_service,
    get_floor,
    get_memory_service,
    get_raise_hand_queue,
    get_room_service,
    get_speech_service,
    get_willingness_service,
)
from app.main import app
from app.memory.service import MemoryService
from app.models.demo_provider import DemoProvider
from app.orchestration import auto_raise
from app.orchestration.conclusion_gate import ConclusionGate
from app.orchestration.floor import FloorGrant
from app.orchestration.queue import RaiseHandQueue
from app.orchestration.willingness import Willingness
from app.persistence.db import get_engine, init_db
from app.rag.embeddings import EmbeddingAdapter, configure_factory
from app.rag.retrieval import RetrievalService
from app.rag.vectorstore import InMemoryVectorStore
from app.services.conclusion_service import ConclusionService
from app.services.config_service import ConfigService
from app.services.room_service import RoomService
from app.services.speech_service import SpeechService
from app.settings import Settings


class ScriptedWillingness:
    """意愿判定的脚本替身：缺省一律不举手，测试按智能体名登记想发言的人。"""

    def __init__(self):
        self.replies: dict[str, Willingness] = {}
        self.asked: list[str] = []
        self.texts: list[str] = []
        self.speakers: list[str | None] = []
        self.error: Exception | None = None

    def check(self, agent, last_message: str, speaker: str | None = None) -> Willingness:
        self.asked.append(agent.agent_id)
        self.texts.append(last_message)
        self.speakers.append(speaker)
        if self.error is not None:
            raise self.error
        return self.replies.get(agent.name, Willingness(False, None))


class RoomAuthClient:
    """按房间自动携带主持人身份的测试客户端。

    既有讨论类用例的语义本来就是「主持人做这件事」——实施前所有人类消息都归属主持人、
    所有控制动作都没有权限边界。逐处补身份头会把一百多个调用点改成噪声，故收敛到这一层；
    要换身份时用 as_member()。主持人身份从 POST /rooms 的响应里就地记下，经服务直接建的
    房间没有响应可截，用 register_host() 登记。
    """

    def __init__(self, inner, hosts=None, forced=None):
        self._inner = inner
        self._hosts = {} if hosts is None else hosts
        self._forced = forced

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def as_member(self, member_id, token):
        return RoomAuthClient(self._inner, self._hosts, (member_id, token))

    @property
    def raw(self):
        """不带任何成员身份的原始客户端：验证「无身份被拒」时用它。"""
        return self._inner

    def register_host(self, room_id, member_id, token):
        self._hosts[room_id] = (member_id, token)

    @staticmethod
    def _room_of(path) -> str | None:
        parts = [p for p in str(path).split("?")[0].split("/") if p]
        return parts[1] if len(parts) > 1 and parts[0] == "rooms" else None

    def _identity(self, path):
        return self._forced or self._hosts.get(self._room_of(path))

    def _headers(self, path, headers):
        h = dict(headers or {})
        ident = self._identity(path)
        if ident:
            h.setdefault("X-Member-Id", ident[0])
            h.setdefault("X-Member-Token", ident[1])
        return h

    def _remember(self, path, res):
        if str(path) == "/rooms" and getattr(res, "status_code", 0) == 201:
            body = res.json()
            self._hosts[body["room_id"]] = (body["host_member_id"], body["host_token"])
        return res

    def _send(self, method, path, kw):
        res = getattr(self._inner, method)(path, headers=self._headers(path, kw.pop("headers", None)), **kw)
        return self._remember(path, res) if method == "post" else res

    def get(self, path, **kw):
        return self._send("get", path, kw)

    def post(self, path, **kw):
        return self._send("post", path, kw)

    def patch(self, path, **kw):
        return self._send("patch", path, kw)

    def delete(self, path, **kw):
        return self._send("delete", path, kw)

    def stream(self, method, path, **kw):
        return self._inner.stream(
            method, path, headers=self._headers(path, kw.pop("headers", None)), **kw
        )

    def websocket_connect(self, path, **kw):
        ident = self._identity(path)
        if ident:
            sep = "&" if "?" in str(path) else "?"
            path = f"{path}{sep}member_id={ident[0]}&token={ident[1]}"
        return self._inner.websocket_connect(path, **kw)


@pytest.fixture(autouse=True)
def env(tmp_path):
    """每个测试用独立临时 SQLite、内存向量库与队列，并覆盖服务依赖。"""
    # 嵌入统一走哈希回退，避免触达真实配置服务与真实模型端点
    configure_factory(None)
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
    floor = FloorGrant()
    gate = ConclusionGate()
    willing = ScriptedWillingness()
    # 轮次的模块态跨用例共享：某例中途失败不该把「正在跑」留给后面的用例
    auto_raise._running.clear()
    auto_raise._again.clear()
    # 自主举手缺省开，但会让每个 WS 用例都多出后台判定与举手事件；测试按需打开
    config.set_auto_raise(False)

    app.dependency_overrides[get_config_service] = lambda: config
    app.dependency_overrides[get_room_service] = lambda: room
    app.dependency_overrides[get_memory_service] = lambda: memory
    app.dependency_overrides[get_speech_service] = lambda: speech
    app.dependency_overrides[get_conclusion_service] = lambda: conclusion
    app.dependency_overrides[get_raise_hand_queue] = lambda: queue
    app.dependency_overrides[get_floor] = lambda: floor
    app.dependency_overrides[get_conclusion_gate] = lambda: gate
    app.dependency_overrides[get_willingness_service] = lambda: willing
    yield SimpleNamespace(
        engine=engine,
        vs=vs,
        config=config,
        room=room,
        memory=memory,
        speech=speech,
        conclusion=conclusion,
        queue=queue,
        floor=floor,
        gate=gate,
        willing=willing,
        data_dir=str(tmp_path),
    )
    app.dependency_overrides.clear()


@pytest.fixture
def client(env):
    # 默认令牌由 init_db 播种为 111111；测试客户端统一携带，令配置类接口通过鉴权。
    return RoomAuthClient(TestClient(app, headers={"X-Admin-Token": "111111"}))


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
    agent = client.post(
        "/agents",
        json={
            "name": "架构师",
            "model_connection_id": conn["conn_id"],
            "model_name": "qwen-max",
            "kb_ids": [kb["kb_id"]],
            "persona": "资深架构师",
            "expertise": "技术债",
            "speaking_style": "先定性再给方案",
        },
    ).json()
    client.post(
        "/documents",
        data={"kb_id": kb["kb_id"]},
        files={"file": ("kb.md", "技术债 延期 砍范围 加人 合规 排期 耦合", "text/markdown")},
    )
    return {"conn": conn, "kb": kb, "agent": agent}


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


@pytest.fixture
def pair(client, seeded):
    """两个在席智能体、已进入讨论的房间：多受话者与自主举手的最小场面。"""
    second = client.post(
        "/agents",
        json={
            "name": "项目经理",
            "model_connection_id": seeded["conn"]["conn_id"],
            "model_name": "qwen-plus",
            "kb_ids": [seeded["kb"]["kb_id"]],
            "persona": "资深项目经理",
            "expertise": "排期与范围",
        },
    ).json()
    room = client.post(
        "/rooms",
        json={
            "proposition": "某功能预计延期两周，应砍范围、加人还是推迟发布？",
            "orchestration_mode": "人类主持控制",
            "agent_ids": [seeded["agent"]["agent_id"], second["agent_id"]],
            "host_name": "陈",
        },
    ).json()
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    return {
        "room": client.get(f"/rooms/{rid}").json(),
        "first": seeded["agent"]["agent_id"],
        "second": second["agent_id"],
        "host_id": room["host_member_id"],
        "host_token": room["host_token"],
    }
