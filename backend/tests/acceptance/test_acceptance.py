from app.domain.enums import SourceType


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


def test_a4_conclusion_seven_sections(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    doc = client.post(f"/rooms/{room['room_id']}/conclusion").json()
    assert set(doc["sections"].keys()) == {
        "命题",
        "关键观点",
        "分歧点",
        "初步结论／建议",
        "行动项与负责人",
        "待定项",
        "风险",
    }


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
        def chat(self, *a, **k):
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
