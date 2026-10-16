"""阶段结论生成期间：全房间等待事件与暂停发言（门闩）。

生成期间暂停发言的口径是双通道一致：WS 的 point／release／human_message 与
REST 的 /messages、/speak、/speak-stream、/raise-hand/{id}/release 全部拒绝；
举手入队与撤回不拦——举手不产生发言。开始与完成两个事件向全房间广播，客户端
据此呈现等待模态并在成功后自动打开产出模态。
"""

from app.orchestration.conclusion_gate import CONCLUDING_DETAIL


def _drain_until_done(ws, limit=20):
    events = []
    for _ in range(limit):
        d = ws.receive_json()
        events.append(d)
        if d.get("type") == "conclusion_done":
            return events
    return events


def test_conclusion_broadcasts_started_and_done(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(
        f"/rooms/{rid}/speak", json={"agent_id": seeded["agent"]["agent_id"]}
    )
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        r = client.post(f"/rooms/{rid}/conclusion")
        events = _drain_until_done(ws)
    assert r.status_code == 201
    assert [e["type"] for e in events[:1]] == ["conclusion_started"]
    assert events[-1] == {"type": "conclusion_done", "ok": True}
    assert env.gate.active(rid) is False


def test_conclusion_failure_broadcasts_done_false(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        r = client.post(f"/rooms/{rid}/conclusion")
        events = _drain_until_done(ws)
    assert r.status_code == 422
    assert events[0]["type"] == "conclusion_started"
    assert events[-1] == {"type": "conclusion_done", "ok": False}
    assert env.gate.active(rid) is False


def test_second_conclusion_while_generating_rejected(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(
        f"/rooms/{rid}/speak", json={"agent_id": seeded["agent"]["agent_id"]}
    )
    env.gate.begin(rid)
    r = client.post(f"/rooms/{rid}/conclusion")
    assert r.status_code == 422
    assert r.json()["detail"] == CONCLUDING_DETAIL
    env.gate.end(rid)


def test_speech_paused_while_concluding_on_both_channels(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    agent = seeded["agent"]["agent_id"]
    env.gate.begin(rid)

    # REST 发言路径全被拒
    assert (
        client.post(f"/rooms/{rid}/messages", json={"content": "你好"}).status_code
        == 422
    )
    assert client.post(f"/rooms/{rid}/speak", json={"agent_id": agent}).status_code == 422
    assert (
        client.post(f"/rooms/{rid}/speak-stream", json={"agent_id": agent}).status_code
        == 422
    )

    # 举手入队不拦（举手不是发言），放行才被拦
    hand = client.post(
        f"/rooms/{rid}/raise-hand", json={"agent_id": agent, "intent": "补充一点"}
    )
    assert hand.status_code == 201
    assert (
        client.post(f"/rooms/{rid}/raise-hand/{hand.json()['raise_id']}/release").status_code
        == 422
    )

    # WS 三条触发发言的上行都被拒，且口径与 REST 同文案
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": agent})
        assert ws.receive_json() == {"type": "rejected", "detail": CONCLUDING_DETAIL}
        ws.send_json({"type": "human_message", "content": "你好"})
        assert ws.receive_json() == {"type": "rejected", "detail": CONCLUDING_DETAIL}
        ws.send_json({"type": "release", "raise_id": hand.json()["raise_id"]})
        assert ws.receive_json() == {"type": "rejected", "detail": CONCLUDING_DETAIL}

    # 释放门闩后恢复可发言
    env.gate.end(rid)
    assert client.post(f"/rooms/{rid}/speak", json={"agent_id": agent}).status_code == 201
    assert (
        client.post(f"/rooms/{rid}/raise-hand/{hand.json()['raise_id']}/release").status_code
        == 201
    )
