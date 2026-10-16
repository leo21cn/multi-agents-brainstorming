import json

from app.models.provider import CONTENT, END, THOUGHT, StreamPiece


class ScriptedProvider:
    def __init__(self, pieces):
        self._pieces = pieces

    def stream_chat(self, model_name, messages, pace=None, **kw):
        yield from self._pieces


def _parse(body: str):
    """把 SSE 文本解析为 (事件名, data) 序列。"""
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        events.append((name, data))
    return events


def _post_stream(client, room_id, agent_id):
    with client.stream(
        "POST", f"/rooms/{room_id}/speak-stream", json={"agent_id": agent_id}
    ) as r:
        return r.status_code, r.headers.get("content-type", ""), "".join(r.iter_text())


def test_speak_stream_emits_stage_delta_then_message(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")

    status, ctype, body = _post_stream(client, rid, seeded["agent"]["agent_id"])
    assert status == 200
    assert ctype.startswith("text/event-stream")

    events = _parse(body)
    names = [n for n, _ in events]
    assert names[0] == "stage"
    # 离线演示提供方不产思考块，故「思考中」阶段不得出现——钉住惰性阶段下发
    assert names.count("stage") == 2
    assert names.index("delta") < names.index("message")
    assert names.index("stage") < names.index("delta")
    assert names.count("message") == 1
    assert "error" not in names

    deltas = [d["delta"] for n, d in events if n == "delta"]
    assert len(deltas) > 1
    final = dict(events)["message"]
    assert "".join(deltas) == final["message"]["content"]
    assert len({d["stream_id"] for _, d in events}) == 1

    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert len(msgs) == 1
    assert msgs[0]["content"] == final["message"]["content"]


def test_speak_stream_checks_identity_before_the_room_exists(client, seeded):
    """身份校验先于房间查找：不向匿名调用者泄露房间号是否有效。"""
    r = client.post("/rooms/room_nope/speak-stream", json={"agent_id": seeded["agent"]["agent_id"]})
    assert r.status_code == 401


def test_speak_stream_ended_room_returns_422(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    client.post(f"/rooms/{rid}/end")
    r = client.post(f"/rooms/{rid}/speak-stream", json={"agent_id": seeded["agent"]["agent_id"]})
    assert r.status_code == 422
    assert client.get(f"/rooms/{rid}/messages").json() == []


def test_speak_stream_unknown_agent_emits_error_event(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    status, _, body = _post_stream(client, rid, "agent_nope")
    assert status == 200
    events = _parse(body)
    assert [n for n, _ in events][-1] == "error"
    assert dict(events)["error"]["detail"] == "agent not found"
    assert client.get(f"/rooms/{rid}/messages").json() == []


def test_speak_remains_non_streaming(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    r = client.post(f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    assert r.status_code == 201
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["content"]


def test_speak_in_ended_room_returns_422(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/end")
    r = client.post(f"/rooms/{rid}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    assert r.status_code == 422


def _break_retrieval(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("embedding endpoint unreachable")

    monkeypatch.setattr(env.speech.retrieval, "retrieve", boom)


def test_speak_stream_emits_error_event_when_retrieval_fails(client, seeded, make_room, env, monkeypatch):
    """检索层异常须收束为 error 事件，不得让响应被截断。"""
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    _break_retrieval(env, monkeypatch)

    status, _, body = _post_stream(client, rid, seeded["agent"]["agent_id"])
    assert status == 200
    events = _parse(body)
    assert [n for n, _ in events][-1] == "error"
    assert "检索失败" in dict(events)["error"]["detail"]
    assert client.get(f"/rooms/{rid}/messages").json() == []


def test_speak_stream_emits_error_event_on_unexpected_failure(client, seeded, make_room, env, monkeypatch):
    room = make_room(seeded["agent"]["agent_id"])

    def boom(*a, **k):
        raise ValueError("unexpected internal failure")

    monkeypatch.setattr(env.speech, "generate_stream", boom)
    status, _, body = _post_stream(client, room["room_id"], seeded["agent"]["agent_id"])
    assert status == 200
    events = _parse(body)
    assert [n for n, _ in events] == ["error"]
    assert "unexpected internal failure" in dict(events)["error"]["detail"]


def test_speak_returns_502_when_retrieval_fails(client, seeded, make_room, env, monkeypatch):
    room = make_room(seeded["agent"]["agent_id"])
    _break_retrieval(env, monkeypatch)
    r = client.post(f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]})
    assert r.status_code == 502
    assert "检索失败" in r.json()["detail"]


def test_release_returns_502_when_retrieval_fails(client, seeded, make_room, env, monkeypatch):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    _break_retrieval(env, monkeypatch)
    rh = client.post(f"/rooms/{rid}/raise-hand", json={"agent_id": seeded["agent"]["agent_id"]}).json()
    r = client.post(f"/rooms/{rid}/raise-hand/{rh['raise_id']}/release")
    assert r.status_code == 502
    assert "检索失败" in r.json()["detail"]


def test_speak_stream_emits_thought_frame(client, seeded, make_room, env):
    """降级通道须呈现与实时通道同样的思考帧序列。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    env.speech.provider_factory = lambda a: ScriptedProvider(
        [
            StreamPiece(THOUGHT, "想一想"),
            StreamPiece(CONTENT, "对外观点"),
            StreamPiece(END, "", False),
        ]
    )

    status, _, body = _post_stream(client, rid, agent_id)

    assert status == 200
    events = _parse(body)
    names = [n for n, _ in events]
    assert "thought" in names
    assert names.index("thought") < names.index("delta")
    assert "".join(d["delta"] for n, d in events if n == "thought") == "想一想"
    assert dict(events)["message"]["message"]["content"] == "对外观点"
    assert len({d["stream_id"] for _, d in events}) == 1


def test_speak_stream_message_carries_truncated(client, seeded, make_room, env):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    env.speech.provider_factory = lambda a: ScriptedProvider(
        [StreamPiece(CONTENT, "甲。" * 60), StreamPiece(END, "", True)]
    )

    _, _, body = _post_stream(client, rid, agent_id)

    final = dict(_parse(body))["message"]
    assert final["truncated"] is True
    assert final["message"]["truncated"] is True
    assert client.get(f"/rooms/{rid}/messages").json()[0]["truncated"] is True


def test_speak_stream_anchors_focus_by_directed_from(client, env, seeded, make_room):
    """降级通道同样带锚点：WS 不可用时多受话者依次应答的焦点不能丢。"""
    from app.domain.enums import SourceType

    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    ask = env.memory.append_message(
        rid,
        SourceType.HUMAN,
        room["host_member_id"],
        "回滚成本怎么算？",
        addressee_agent_ids=[agent_id],
    )
    env.memory.append_message(rid, SourceType.AGENT, agent_id, "先答一句。")
    seen = {}
    original = env.speech.generate_stream

    def spy(room_id, who, intent=None, directed_from=None):
        seen["directed_from"] = directed_from
        return original(room_id, who, intent, directed_from)

    env.speech.generate_stream = spy
    try:
        with client.stream(
            "POST",
            f"/rooms/{rid}/speak-stream",
            json={"agent_id": agent_id, "directed_from": ask.message_id},
        ) as r:
            status, body = r.status_code, "".join(r.iter_text())
    finally:
        env.speech.generate_stream = original

    assert status == 200
    assert seen["directed_from"] == ask.message_id
    assert [n for n, _ in _parse(body)][-1] == "message"
