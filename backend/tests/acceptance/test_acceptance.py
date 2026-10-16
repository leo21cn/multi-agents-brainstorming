from fastapi.testclient import TestClient

from app.domain.enums import SourceType
from app.main import app


def test_a1_no_unauthorized_speech(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    assert client.get(f"/rooms/{rid}/messages").json() == []
    client.post(f"/rooms/{rid}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    assert len(client.get(f"/rooms/{rid}/messages").json()) == 1


def test_a2_raise_hand_closure(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid, agent = room["room_id"], seeded["agent"]["agent_id"]

    rh = client.post(f"/rooms/{rid}/raise-hand", json={"agent_id": agent}).json()
    assert len(client.get(f"/rooms/{rid}/raise-hand").json()) == 1
    client.post(f"/rooms/{rid}/raise-hand/{rh['raise_id']}/release")
    assert client.get(f"/rooms/{rid}/raise-hand").json() == []
    assert len(client.get(f"/rooms/{rid}/messages").json()) == 1

    rh2 = client.post(f"/rooms/{rid}/raise-hand", json={"agent_id": agent}).json()
    client.post(f"/rooms/{rid}/raise-hand/{rh2['raise_id']}/dismiss")
    assert client.get(f"/rooms/{rid}/raise-hand").json() == []
    assert len(client.get(f"/rooms/{rid}/messages").json()) == 1


def test_a3_config_effect(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    body = client.post(
        f"/rooms/{room['room_id']}/speak",
        json={"agent_id": seeded["agent"]["agent_id"], "intent": "延期根因"},
    ).json()
    assert set(body["cited_kb_ids"]) <= {seeded["kb"]["kb_id"]}


def test_a4_conclusion_sections(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    doc = client.post(f"/rooms/{room['room_id']}/conclusion").json()
    keys = list(doc["sections"].keys())
    assert keys[:4] == ["命题", "关键观点", "分歧点", "初步结论／建议"]
    extra = keys[4:]
    assert len(extra) <= 10
    tail = [doc["sections"][k] for k in keys[3:]]
    assert all(isinstance(t, str) and t.strip() for t in tail)
    # 综合章为成段文字：出现指向他章的占位句即视为没有综合
    assert not [t for t in tail if "见关键观点" in t or "见分歧点" in t]
    for title in extra:
        assert title not in {"命题", "关键观点", "分歧点", "初步结论／建议"}
        assert len(title) <= 20


def test_a5_annotation_completeness(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    doc = client.post(f"/rooms/{room['room_id']}/conclusion").json()
    assert doc["source_annotations"]
    for ann in doc["source_annotations"]:
        assert ann["agent"] and ann["kbs"]
    for item in doc["sections"]["关键观点"] + doc["sections"]["分歧点"]:
        assert item["source"]["agent"] and item["source"]["kbs"]


def test_a6_export_count_match(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    client.post(f"/rooms/{rid}/messages", json={"content": "人类补充"})
    exp = client.post(f"/rooms/{rid}/export").json()
    assert exp["entry_count"] == len(client.get(f"/rooms/{rid}/messages").json())


def test_a7_rolling_summary_preserves_proposition(client, seeded, env, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid, agent = room["room_id"], seeded["agent"]["agent_id"]
    for _ in range(80):
        env.memory.append_message(rid, SourceType.HUMAN, "m", "内容" * 60)
    assert env.memory.maybe_summarize(rid) is not None
    ctx = env.memory.build_context("命题X", rid, agent)
    assert any("命题X" in m["content"] for m in ctx)


def test_a8_model_failure_reported(client, seeded, env, make_room):
    room = make_room(seeded["agent"]["agent_id"])

    class Boom:
        def stream_chat(self, *a, **k):
            raise RuntimeError("timeout")

    env.speech.provider_factory = lambda a: Boom()
    r = client.post(
        f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]}
    )
    assert r.status_code == 502


def test_a8_empty_memory_conclusion_rejected(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    assert client.post(f"/rooms/{room['room_id']}/conclusion").status_code == 422


def test_a9_init_gate(client, seeded):
    assert client.get("/system/init-status").json()["ready"] is True


def test_a9_init_gate_blocks_when_empty(client):
    assert client.get("/system/init-status").json()["ready"] is False
    r = client.post(
        "/rooms",
        json={
            "proposition": "p",
            "orchestration_mode": "人类主持控制",
            "agent_ids": ["a"],
            "host_name": "陈",
        },
    )
    assert r.status_code == 422


def test_a10_admin_token_gates_config(client):
    anon = TestClient(app)
    assert anon.get("/model-connections").status_code == 401
    assert anon.get("/documents").status_code == 401
    assert anon.post("/knowledge-bases", json={"name": "x", "dimension": "技术架构"}).status_code == 401
    assert anon.get("/system/init-status").status_code == 200
    assert anon.get("/agents").status_code == 200
    assert anon.get("/knowledge-bases").status_code == 200
    assert anon.post("/admin/login", json={"token": "111111"}).status_code == 200
    assert anon.post("/admin/login", json={"token": "错"}).status_code == 401
    assert client.get("/model-connections").status_code == 200
    assert client.patch("/admin/token", json={"new_token": "222222"}).status_code == 200
    assert TestClient(app, headers={"X-Admin-Token": "222222"}).get("/model-connections").status_code == 200
    assert client.get("/model-connections").status_code == 401


def test_a11_home_guard_reads_init_status(client):
    body = client.get("/system/init-status").json()
    assert body["ready"] is False
    assert body["kb_count"] == 0 and body["bound_agent_count"] == 0


def test_a11_home_guard_stays_when_ready(client, seeded):
    assert client.get("/system/init-status").json()["ready"] is True
