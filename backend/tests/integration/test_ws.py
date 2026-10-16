import threading

from app.api import ws as ws_mod
from app.models.provider import CONTENT, END, THOUGHT, StreamPiece
from app.orchestration.willingness import Willingness


class ScriptedProvider:
    def __init__(self, pieces):
        self._pieces = pieces

    def stream_chat(self, model_name, messages, pace=None, **kw):
        yield from self._pieces


def _drain(ws, stop, limit=80):
    """收取事件直到 stop(events) 为真；limit 用于防止实现缺陷导致测试挂起。"""
    events = []
    for _ in range(limit):
        evt = ws.receive_json()
        events.append(evt)
        if stop(events):
            break
    return events


def _finished(events):
    return any(e["type"] in {"message", "speech_failed"} for e in events)


def test_ws_point_streams_stages_and_deltas_before_message(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
        events = _drain(ws, _finished)

    kinds = [e["type"] for e in events]
    # 离线演示提供方不产思考块，故「思考中」阶段不得出现——钉住惰性阶段下发
    assert kinds.count("stage") == 2
    assert kinds[0] == "stage"
    assert "message_delta" in kinds
    assert kinds.index("message_delta") < kinds.index("message")
    assert kinds.index("stage") < kinds.index("message_delta")

    deltas = [e["delta"] for e in events if e["type"] == "message_delta"]
    assert len(deltas) > 1
    final = next(e for e in events if e["type"] == "message")
    assert "".join(deltas) == final["message"]["content"]
    assert final["message"]["source_type"] == "智能体"
    # stage／message_delta／message 共用同一 stream_id，前端据此定位并替换流式气泡
    stream_ids = {e["stream_id"] for e in events if e["type"] != "speech_failed"}
    assert len(stream_ids) == 1
    assert all(e["agent_id"] == seeded["agent"]["agent_id"] for e in events if e["type"] == "stage")


def test_ws_point_broadcasts_message(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
        events = _drain(ws, _finished)
    assert events[-1]["type"] in {"message", "speech_failed"}


def test_ws_stays_responsive_while_generating(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
        ws.send_json({"type": "room_state"})
        events = _drain(ws, lambda ev: _finished(ev) and any(e["type"] == "room_state" for e in ev))

    kinds = [e["type"] for e in events]
    assert "room_state" in kinds
    state = next(e for e in events if e["type"] == "room_state")
    assert state["room"]["room_id"] == rid


def test_ws_raise_hand_event(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "raise_hand", "agent_id": seeded["agent"]["agent_id"]})
        evt = ws.receive_json()
        assert evt["type"] == "raise_hand"


def test_ws_release_streams_speech(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    agent_id = seeded["agent"]["agent_id"]
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "raise_hand", "agent_id": agent_id})
        raise_id = ws.receive_json()["raise_id"]
        ws.send_json({"type": "release", "raise_id": raise_id})
        events = _drain(ws, _finished)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "raise_hand_update"
    assert "stage" in kinds
    assert "message_delta" in kinds
    assert kinds.index("message_delta") < kinds.index("message")
    assert events[-1]["message"]["source_id"] == agent_id


def test_ws_release_keeps_raised_intent(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    seen = {}
    original = env.speech.generate_stream

    def spy(room_id, agent_id, intent=None, directed_from=None):
        seen["intent"] = intent
        return original(room_id, agent_id, intent, directed_from)

    env.speech.generate_stream = spy
    try:
        with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
            ws.send_json(
                {
                    "type": "raise_hand",
                    "agent_id": seeded["agent"]["agent_id"],
                    "intent": "补充成本视角",
                }
            )
            raise_id = ws.receive_json()["raise_id"]
            ws.send_json({"type": "release", "raise_id": raise_id})
            _drain(ws, _finished)
    finally:
        env.speech.generate_stream = original

    assert seen["intent"] == "补充成本视角"


def test_ws_point_while_busy_is_queued(client, seeded, make_room):
    """忙时点名排队而不丢弃：主持人的显式指令不该因上一次发言未收束而消失。"""
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    ws_mod._inflight[rid] = seeded["agent"]["agent_id"]
    try:
        with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
            ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
            ws.send_json({"type": "room_state"})
            events = _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))
        queued = list(ws_mod._pending.get(rid, []))
    finally:
        ws_mod._inflight.pop(rid, None)
        ws_mod._pending.pop(rid, None)

    assert not [e for e in events if e["type"] == "speech_failed"]
    assert [p.agent_id for p in queued] == [seeded["agent"]["agent_id"]]


def test_ws_inflight_cleared_after_generation(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
        _drain(ws, _finished)
        ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
        events = _drain(ws, lambda ev: sum(1 for e in ev if e["type"] == "message") >= 1)

    assert events[-1]["type"] == "message"
    assert rid not in ws_mod._inflight


def test_ws_reports_retrieval_failure(client, seeded, make_room, env):
    """检索失败须在阶段事件之后收束为 speech_failed，并释放单人发言守卫。"""
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]

    class BoomRetrieval:
        def retrieve(self, query, kb_ids):
            raise RuntimeError("embedding endpoint unreachable")

    env.speech.retrieval = BoomRetrieval()
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
        events = _drain(ws, _finished)

    kinds = [e["type"] for e in events]
    assert kinds == ["stage", "speech_failed"]
    assert "检索失败" in events[-1]["detail"]
    assert rid not in ws_mod._inflight
    assert env.memory.all_messages(rid) == []


def test_ws_human_message_source_is_host_member(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "记一下"})
        evt = ws.receive_json()
    assert evt["message"]["source_id"] == room["host_member_id"]


def test_ws_rejects_human_message_in_ended_room(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/end")
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "还有一句"})
        evt = ws.receive_json()
    assert evt["type"] == "rejected"
    assert evt["detail"] == "房间已结束，不再新增发言"
    assert env.memory.all_messages(rid) == []


def test_ws_rejects_addressee_outside_roster(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json(
            {
                "type": "human_message",
                "content": "@外人",
                "addressee_agent_ids": ["agent_not_in_room"],
            }
        )
        evt = ws.receive_json()
    assert evt["type"] == "rejected"
    assert evt["detail"] == "受话者不在房间阵容内"
    assert env.memory.all_messages(rid) == []


def test_ws_directed_human_message_triggers_speech(client, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json(
            {
                "type": "human_message",
                "content": "@架构师 回滚成本怎么算？",
                "addressee_agent_ids": [agent_id],
            }
        )
        events = _drain(
            ws,
            lambda ev: any(
                e["type"] == "message" and e["message"]["source_type"] == "智能体" for e in ev
            ),
        )

    kinds = [e["type"] for e in events]
    assert kinds[0] == "message"
    assert events[0]["message"]["addressee_agent_ids"] == [agent_id]
    assert "stage" in kinds and "message_delta" in kinds
    assert kinds.index("message_delta") < kinds.index("message", 1)
    assert events[-1]["message"]["source_id"] == agent_id


def test_ws_directed_message_keeps_single_stream_id(client, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json(
            {"type": "human_message", "content": "问一句", "addressee_agent_ids": [agent_id]}
        )
        events = _drain(
            ws,
            lambda ev: any(
                e["type"] == "message" and e["message"]["source_type"] == "智能体" for e in ev
            ),
        )

    stream_ids = {e["stream_id"] for e in events if "stream_id" in e}
    assert len(stream_ids) == 1


def test_ws_undirected_human_message_does_not_trigger(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "各位"})
        ws.send_json({"type": "room_state"})
        events = _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))

    assert [e["type"] for e in events] == ["message", "room_state"]


def test_ws_directed_message_while_busy_is_queued(client, seeded, make_room, env):
    """忙时定向提问既不丢问题也不丢应答：问题落库，应答排队，并锚定这条提问。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    ws_mod._inflight[rid] = agent_id
    try:
        with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
            ws.send_json(
                {
                    "type": "human_message",
                    "content": "问一句",
                    "addressee_agent_ids": [agent_id],
                }
            )
            events = _drain(ws, lambda ev: any(e["type"] == "message" for e in ev))
            ws.send_json({"type": "room_state"})
            events += _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))
        queued = list(ws_mod._pending.get(rid, []))
    finally:
        ws_mod._inflight.pop(rid, None)
        ws_mod._pending.pop(rid, None)

    assert [e["type"] for e in events if e["type"] in {"message", "speech_failed"}] == ["message"]
    assert len(env.memory.all_messages(rid)) == 1
    assert [p.agent_id for p in queued] == [agent_id]
    assert queued[0].directed_from == events[0]["message"]["message_id"]


def test_ws_release_while_busy_keeps_intent_in_queue(client, seeded, make_room):
    """放行忙时不再连意图一起丢掉：出队后进入待发言 FIFO，前一次收束即触发。"""
    agent_id = seeded["agent"]["agent_id"]
    rid = make_room(agent_id)["room_id"]
    ws_mod._inflight[rid] = agent_id
    try:
        with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
            ws.send_json({"type": "raise_hand", "agent_id": agent_id, "intent": "补充成本视角"})
            raise_id = ws.receive_json()["raise_id"]
            ws.send_json({"type": "release", "raise_id": raise_id})
            ws.send_json({"type": "room_state"})
            events = _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))
        queued = list(ws_mod._pending.get(rid, []))
    finally:
        ws_mod._inflight.pop(rid, None)
        ws_mod._pending.pop(rid, None)

    assert not [e for e in events if e["type"] == "speech_failed"]
    assert [(p.agent_id, p.intent) for p in queued] == [(agent_id, "补充成本视角")]
    assert client.get(f"/rooms/{rid}/raise-hand").json() == []


def test_ws_two_addressees_answer_in_order(client, seeded, pair, env):
    """A-19：@ 两个智能体，两个都要答，且按书写次序依次答。"""
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json(
            {
                "type": "human_message",
                "content": "@架构师 @项目经理 两位怎么看？",
                "addressee_agent_ids": [pair["first"], pair["second"]],
            }
        )
        events = _drain(
            ws,
            lambda ev: sum(
                1
                for e in ev
                if e["type"] == "message" and e["message"]["source_type"] == "智能体"
            )
            >= 2,
            limit=200,
        )

    answers = [
        e["message"]
        for e in events
        if e["type"] == "message" and e["message"]["source_type"] == "智能体"
    ]
    assert [m["source_id"] for m in answers] == [pair["first"], pair["second"]]
    question = next(
        e["message"]
        for e in events
        if e["type"] == "message" and e["message"]["source_type"] == "人类"
    )
    assert question["addressee_agent_ids"] == [pair["first"], pair["second"]]
    assert [m["addressee_agent_ids"] for m in answers] == [[], []]
    # 两次应答各用一条 stream_id；A-12「增量拼接与落库一致」在 FIFO 下逐条成立
    stream_ids = {e["stream_id"] for e in events if e["type"] == "stage"}
    assert len(stream_ids) == 2
    for sid in stream_ids:
        joined = "".join(
            e["delta"]
            for e in events
            if e["type"] == "message_delta" and e.get("stream_id") == sid
        )
        final = next(e for e in events if e["type"] == "message" and e.get("stream_id") == sid)
        assert joined == final["message"]["content"]
    assert len(env.memory.all_messages(rid)) == 3


def test_ws_second_addressee_sees_the_same_question_as_focus(client, seeded, pair, env):
    """第二个应答者的上下文焦点仍是那条提问，而不是第一个人的回答。"""
    rid = pair["room"]["room_id"]
    seen = []
    original = env.speech.generate_stream

    def spy(room_id, agent_id, intent=None, directed_from=None):
        seen.append((agent_id, directed_from))
        return original(room_id, agent_id, intent, directed_from)

    env.speech.generate_stream = spy
    try:
        with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
            ws.send_json(
                {
                    "type": "human_message",
                    "content": "@架构师 @项目经理 两位怎么看？",
                    "addressee_agent_ids": [pair["first"], pair["second"]],
                }
            )
            _drain(
                ws,
                lambda ev: sum(
                    1
                    for e in ev
                    if e["type"] == "message" and e["message"]["source_type"] == "智能体"
                )
                >= 2,
                limit=200,
            )
    finally:
        env.speech.generate_stream = original

    assert [a for a, _ in seen] == [pair["first"], pair["second"]]
    assert len({d for _, d in seen}) == 1 and seen[0][1] is not None


def test_ws_streams_thought_delta_before_content(client, seeded, make_room, env):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: ScriptedProvider(
        [
            StreamPiece(THOUGHT, "想一想"),
            StreamPiece(CONTENT, "对外"),
            StreamPiece(CONTENT, "观点"),
            StreamPiece(END, "", False),
        ]
    )

    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": agent_id})
        events = _drain(ws, _finished)

    kinds = [e["type"] for e in events]
    assert "thought_delta" in kinds
    assert kinds.index("thought_delta") < kinds.index("message_delta")
    assert "".join(e["delta"] for e in events if e["type"] == "thought_delta") == "想一想"
    assert "".join(e["delta"] for e in events if e["type"] == "message_delta") == "对外观点"
    final = next(e for e in events if e["type"] == "message")
    assert final["message"]["content"] == "对外观点"
    assert final["stream_id"] == events[0]["stream_id"]


def test_ws_message_carries_truncated_flag(client, seeded, make_room, env):
    agent_id = seeded["agent"]["agent_id"]

    env.speech.provider_factory = lambda a: ScriptedProvider(
        [StreamPiece(CONTENT, "甲。" * 60), StreamPiece(END, "", True)]
    )
    room = make_room(agent_id)
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": agent_id})
        events = _drain(ws, _finished)
    assert next(e for e in events if e["type"] == "message")["truncated"] is True

    env.speech.provider_factory = lambda a: ScriptedProvider(
        [StreamPiece(CONTENT, "完整一句"), StreamPiece(END, "", False)]
    )
    room = make_room(agent_id)
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": agent_id})
        events = _drain(ws, _finished)
    assert next(e for e in events if e["type"] == "message")["truncated"] is False


def test_ws_thought_delta_shares_stream_id(client, seeded, make_room, env):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: ScriptedProvider(
        [
            StreamPiece(THOUGHT, "想一想"),
            StreamPiece(CONTENT, "观点"),
            StreamPiece(END, "", False),
        ]
    )

    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": agent_id})
        events = _drain(ws, _finished)

    kinds = {"thought_delta", "message_delta", "message"}
    assert {e["stream_id"] for e in events if e["type"] in kinds} == {events[0]["stream_id"]}


def test_ws_release_of_a_withdrawn_hand_reports_why(client, env, pair):
    """A-20：举手已自行撤回，主持人点的是过期条目；静默无回执会让界面卡在乐观占位上。"""
    rid = pair["room"]["room_id"]
    item = env.queue.enqueue(rid, pair["second"], "乙想说", source="auto")

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        env.queue.withdraw(rid, item.raise_id)
        ws.send_json({"type": "release", "raise_id": item.raise_id})
        events = _drain(ws, lambda ev: any(e["type"] == "rejected" for e in ev))

    assert [e["detail"] for e in events if e["type"] == "rejected"] == [
        "举手已不在队列中，可能已随讨论推进被撤回"
    ]
    assert ws_mod._pending.get(rid, []) == []


def test_ws_speaking_agent_is_reserved_from_the_round(client, env, pair):
    """正在发言的人不该被判定再举一次手：他的发言还没落库，上下文里还没有他自己。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "想说")
    rid = pair["room"]["room_id"]
    gate = threading.Event()
    original = env.speech.generate_stream

    def held(room_id, agent_id, intent=None, directed_from=None):
        yield "stage", "检索"
        gate.wait(10)
        yield from original(room_id, agent_id, intent, directed_from)

    env.speech.generate_stream = held
    try:
        with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
            ws.send_json({"type": "point", "agent_id": pair["first"]})
            _drain(ws, lambda ev: any(e["type"] == "stage" for e in ev))
            ws.send_json({"type": "human_message", "content": "谁还有意见？"})
            _drain(ws, lambda ev: any(e["type"] == "raise_hand_update" for e in ev))
            assert pair["first"] not in env.willing.asked
            gate.set()
            _drain(
                ws,
                lambda ev: any(
                    e["type"] == "message" and e["message"]["source_type"] == "智能体"
                    for e in ev
                ),
            )
    finally:
        gate.set()
        env.speech.generate_stream = original

    assert pair["second"] in env.willing.asked
    assert pair["first"] not in env.willing.asked
