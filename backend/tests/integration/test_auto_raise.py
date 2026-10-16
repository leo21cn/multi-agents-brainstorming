import asyncio
import threading
import time

from app.domain.enums import SourceType
from app.orchestration import auto_raise
from app.orchestration.auto_raise import raise_round
from app.orchestration.willingness import Willingness


def _drain(ws, stop, limit=200):
    """收取事件直到 stop(events) 为真；limit 用于防止实现缺陷导致测试挂起。"""
    events = []
    for _ in range(limit):
        evt = ws.receive_json()
        events.append(evt)
        if stop(events):
            break
    return events


async def _record(sink, event):
    sink.append(event)


def _wait_for(predicate, timeout=5.0):
    """轮询等待后台轮次落定：判定在任务中运行，不与上行指令排序。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        time.sleep(0.02)
    return predicate()


def _settle_window(seconds=0.3):
    """否定断言无可等待信号，给后台任务一个固定窗口。"""
    time.sleep(seconds)


def _round_coro(env, rid, *, reserved=(), sink=None):
    """构造一轮判定的协程，由调用方决定在哪个事件循环里跑。"""
    seen = [] if sink is None else sink
    return raise_round(
        rid,
        speech=env.speech,
        queue=env.queue,
        room_service=env.room,
        config=env.config,
        willing=env.willing,
        notify=lambda event: _record(seen, event),
        reserved=lambda: list(reserved),
    )


def _run_round(env, rid, *, reserved=()):
    """跑完一轮判定，返回本轮发出的广播事件。"""
    seen = []
    asyncio.run(_round_coro(env, rid, reserved=reserved, sink=seen))
    return seen


def test_undirected_question_raises_hands_of_willing_agents(client, env, pair):
    """A-18：群发提问后，愿意发言的智能体自行举手；举手不产生任何发言。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "补一句排期风险")
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "谁有意见？"})
        events = _drain(
            ws, lambda ev: any(e["type"] == "raise_hand_update" for e in ev)
        )

    assert not [e for e in events if e["type"] in {"stage", "message_delta"}]
    assert not [
        e
        for e in events
        if e["type"] == "message" and e["message"]["source_type"] == "智能体"
    ]
    assert [m.source_type.value for m in env.memory.all_messages(rid)] == ["人类"]
    queued = client.get(f"/rooms/{rid}/raise-hand").json()
    assert [q["agent_id"] for q in queued] == [pair["second"]]
    assert [q["intent"] for q in queued] == ["补一句排期风险"]
    assert [q["source"] for q in queued] == ["auto"]


def test_agent_speech_also_triggers_a_round(client, env, pair):
    """触发点是「每条新发言」，不限于人类消息；刚发言的人不在此列。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "想说")
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": pair["first"]})
        _drain(ws, lambda ev: any(e["type"] == "raise_hand_update" for e in ev))

    queued = client.get(f"/rooms/{rid}/raise-hand").json()
    assert [q["agent_id"] for q in queued] == [pair["second"]]
    assert pair["first"] not in env.willing.asked


def test_directed_message_does_not_ask_its_own_addressees(client, env, pair):
    """已被点名的受话者不再被问：他们已经在待发言队列里。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "想说")
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json(
            {
                "type": "human_message",
                "content": "@架构师 回滚成本怎么算？",
                "addressee_agent_ids": [pair["first"]],
            }
        )
        _drain(
            ws,
            lambda ev: any(
                e["type"] == "message" and e["message"]["source_type"] == "智能体" for e in ev
            ),
        )

    assert _wait_for(lambda: len(env.willing.asked) >= 1)
    assert set(env.willing.asked) == {pair["second"]}


def test_switch_off_makes_no_judgment_calls(client, env, pair):
    """A-18 的开关面：关掉后一次判定都不发生，界面也不出现自主举手。"""
    env.willing.replies["项目经理"] = Willingness(True, "想说")
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "谁有意见？"})
        events = _drain(ws, lambda ev: any(e["type"] == "message" for e in ev))
        ws.send_json({"type": "room_state"})
        events += _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))

    _settle_window()
    assert env.willing.asked == []
    assert not [e for e in events if e["type"] == "raise_hand_update"]
    assert client.get(f"/rooms/{rid}/raise-hand").json() == []


def test_judgment_failure_is_fail_closed(client, env, pair):
    """模型故障不该变成一条错误举手，也不该中断讨论。"""
    env.config.set_auto_raise(True)
    env.willing.error = RuntimeError("model endpoint unreachable")
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "谁有意见？"})
        events = _drain(ws, lambda ev: any(e["type"] == "message" for e in ev))

    assert _wait_for(lambda: len(env.willing.asked) == 2)
    assert client.get(f"/rooms/{rid}/raise-hand").json() == []
    assert not [e for e in events if e["type"] in {"raise_hand_update", "speech_failed"}]


def test_round_skips_speaker_and_host_filled_hands(env, pair):
    """刚发言的人与主持人代填的举手都不进判定：前者不必对自己表态，后者不归机器改写。"""
    env.config.set_auto_raise(True)
    env.willing.replies = {
        "架构师": Willingness(True, "甲想说"),
        "项目经理": Willingness(True, "乙想说"),
    }
    rid = pair["room"]["room_id"]
    env.queue.enqueue(rid, pair["second"], "主持人代填的意图")
    env.memory.append_message(rid, SourceType.AGENT, pair["first"], "我先说一句。")

    seen = _run_round(env, rid)

    assert seen == []
    assert env.willing.asked == []
    assert [(q.intent, q.source) for q in env.queue.list(rid)] == [
        ("主持人代填的意图", "sim")
    ]


def test_round_skips_reserved_speakers(env, pair):
    """已被主持人安排发言的人不再问一遍，否则会产生一条重复举手。"""
    env.config.set_auto_raise(True)
    env.willing.replies = {
        "架构师": Willingness(True, "甲想说"),
        "项目经理": Willingness(True, "乙想说"),
    }
    rid = pair["room"]["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, "mem_1", "谁有意见？")

    seen = _run_round(env, rid, reserved=[pair["first"], pair["second"]])

    assert seen == [] and env.willing.asked == []


def test_round_skips_room_that_is_not_in_progress(env, pair, client):
    """待开始与已结束的房间不做判定：讨论尚未开始或已经收场，举手无处安放。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "想说")
    rid = pair["room"]["room_id"]
    client.post(f"/rooms/{rid}/end")
    seen = []

    asyncio.run(
        raise_round(
            rid,
            speech=env.speech,
            queue=env.queue,
            room_service=env.room,
            config=env.config,
            willing=env.willing,
            notify=lambda event: _record(seen, event),
        )
    )

    assert seen == [] and env.willing.asked == []


def test_round_coalesces_while_running(env, pair):
    """同房间并发两轮只跑一轮，否则同一批智能体会被反复询问。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "想说")
    rid = pair["room"]["room_id"]
    auto_raise._running.add(rid)

    try:
        seen = _run_round(env, rid)
    finally:
        auto_raise._running.discard(rid)
        auto_raise._again.discard(rid)

    assert seen == [] and env.willing.asked == []


def test_refreshed_hands_keep_their_positions_and_ids(env, pair):
    """A-20：一条新发言之后两条举手都重判，仍想说的原位改写意图。"""
    env.config.set_auto_raise(True)
    env.willing.replies = {
        "架构师": Willingness(True, "甲新意图"),
        "项目经理": Willingness(True, "乙新意图"),
    }
    rid = pair["room"]["room_id"]
    first = env.queue.enqueue(rid, pair["first"], "甲旧意图", source="auto")
    second = env.queue.enqueue(rid, pair["second"], "乙旧意图", source="auto")
    env.memory.append_message(rid, SourceType.HUMAN, "mem_1", "换个角度看呢？")

    seen = _run_round(env, rid)

    hands = env.queue.list(rid)
    assert [h.raise_id for h in hands] == [first.raise_id, second.raise_id]
    assert [h.intent for h in hands] == ["甲新意图", "乙新意图"]
    assert [h.source for h in hands] == ["auto", "auto"]
    assert seen == [{"type": "raise_hand_update"}]


def test_unwilling_hand_is_withdrawn(env, pair):
    """别人把话说完了，原本想补的角度不再新：举手自行撤回，不等主持人驳回。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(False, None)
    rid = pair["room"]["room_id"]
    env.queue.enqueue(rid, pair["second"], "乙旧意图", source="auto")
    env.memory.append_message(
        rid, SourceType.AGENT, pair["first"], "排期风险我已经讲完了。"
    )

    seen = _run_round(env, rid)

    assert env.queue.list(rid) == []
    assert seen == [{"type": "raise_hand_update"}]
    assert env.willing.asked == [pair["second"]]


def test_judgment_failure_leaves_an_existing_hand_untouched(env, pair):
    """判定失败是「没有结论」，不是「不想说」：撤回已有举手会让模型抖动变成一次静默失声。"""
    env.config.set_auto_raise(True)
    env.willing.error = RuntimeError("model endpoint unreachable")
    rid = pair["room"]["room_id"]
    item = env.queue.enqueue(rid, pair["second"], "乙旧意图", source="auto")
    env.memory.append_message(rid, SourceType.HUMAN, "mem_1", "还有别的角度吗？")

    seen = _run_round(env, rid)

    hands = env.queue.list(rid)
    assert [(h.raise_id, h.intent) for h in hands] == [(item.raise_id, "乙旧意图")]
    assert seen == []


def test_host_simulated_hand_survives_a_round_that_would_withdraw_it(env, pair):
    """同一条上下文下自主举手会被撤回，主持人代填的那条不受影响，也不被判定。"""
    env.config.set_auto_raise(True)
    env.willing.replies = {
        "架构师": Willingness(False, None),
        "项目经理": Willingness(False, None),
    }
    rid = pair["room"]["room_id"]
    sim = env.queue.enqueue(rid, pair["second"], "主持人代填的意图")
    auto = env.queue.enqueue(rid, pair["first"], "甲旧意图", source="auto")
    env.memory.append_message(rid, SourceType.HUMAN, "mem_1", "还有别的角度吗？")

    seen = _run_round(env, rid)

    hands = env.queue.list(rid)
    assert [h.raise_id for h in hands] == [sim.raise_id]
    assert auto.raise_id not in [h.raise_id for h in hands]
    assert env.willing.asked == [pair["first"]]
    assert seen == [{"type": "raise_hand_update"}]


def test_hand_refresh_reaches_the_client_as_a_queue_update(client, env, pair):
    """A-20：刷新经实时通道到达界面，主持人不必重开页面。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "补一句排期风险")
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "谁有意见？"})
        _drain(ws, lambda ev: any(e["type"] == "raise_hand_update" for e in ev))
        first = client.get(f"/rooms/{rid}/raise-hand").json()
        env.willing.replies["项目经理"] = Willingness(True, "改口谈砍范围")
        ws.send_json({"type": "human_message", "content": "范围能砍到什么程度？"})
        events = _drain(
            ws, lambda ev: any(e["type"] == "raise_hand_update" for e in ev)
        )

    queued = client.get(f"/rooms/{rid}/raise-hand").json()
    assert [q["intent"] for q in queued] == ["改口谈砍范围"]
    assert [q["source"] for q in queued] == ["auto"]
    assert [q["raise_id"] for q in queued] == [q["raise_id"] for q in first]
    assert [e["type"] for e in events if e["type"] == "raise_hand"] == []


def test_judgments_run_concurrently(env, pair):
    """逐个判定会把刷新延迟乘以在席人数；两个判定须真正重叠。

    栅栏只有一方到达就会超时，超时后 `asked` 里一个名字都不会有，串行实现当场暴露。
    """
    env.config.set_auto_raise(True)
    rid = pair["room"]["room_id"]
    barrier = threading.Barrier(2, timeout=5)
    original = env.willing.check

    def gated(agent, last_message, speaker=None):
        barrier.wait()
        return original(agent, last_message, speaker)

    env.willing.check = gated
    env.memory.append_message(rid, SourceType.HUMAN, "mem_1", "谁有意见？")
    try:
        _run_round(env, rid)
    finally:
        env.willing.check = original

    assert set(env.willing.asked) == {pair["first"], pair["second"]}


def test_results_are_applied_in_roster_order_not_completion_order(env, pair):
    """判定完成的先后不该决定队列次序：否则同一批人每次刷新后排法都不一样。"""
    env.config.set_auto_raise(True)
    env.willing.replies = {
        "架构师": Willingness(True, "甲想说"),
        "项目经理": Willingness(True, "乙想说"),
    }
    rid = pair["room"]["room_id"]
    roster = env.room.selected_agent_ids(rid)
    original = env.willing.check

    def slowed(agent, last_message, speaker=None):
        # 阵容次序在前的那位反而更慢返回：若按完成次序入队，队列次序就会翻过来
        if agent.agent_id == roster[0]:
            time.sleep(0.3)
        return original(agent, last_message, speaker)

    env.willing.check = slowed
    env.memory.append_message(rid, SourceType.HUMAN, "mem_1", "谁有意见？")
    try:
        _run_round(env, rid)
    finally:
        env.willing.check = original

    assert [h.agent_id for h in env.queue.list(rid)] == roster


def test_message_landing_during_a_round_triggers_a_trailing_round(env, pair):
    """轮次跑着的时候又来了新发言：合并重入不能把这次刷新整个丢掉。"""
    env.config.set_auto_raise(True)
    env.willing.replies["项目经理"] = Willingness(True, "想说")
    rid = pair["room"]["room_id"]
    gate = threading.Event()
    original = env.willing.check

    def gated(agent, last_message, speaker=None):
        if agent.name == "项目经理":
            gate.wait(10)
        return original(agent, last_message, speaker)

    async def scenario():
        first = asyncio.create_task(_round_coro(env, rid))
        await asyncio.sleep(0.1)
        env.memory.append_message(rid, SourceType.AGENT, pair["first"], "第二条")
        second = asyncio.create_task(_round_coro(env, rid))
        gate.set()
        await asyncio.gather(first, second)

    env.willing.check = gated
    env.memory.append_message(rid, SourceType.HUMAN, "mem_1", "第一条")
    try:
        asyncio.run(scenario())
    finally:
        env.willing.check = original

    assert "第二条" in env.willing.texts
    assert auto_raise._running == set() and auto_raise._again == set()


def test_a_round_leaves_member_hands_alone(client, env, pair):
    """成员举手的意图是他自己写的，判定无权改写，更无权替他撤回。"""
    env.config.set_auto_raise(True)
    env.willing.replies = {
        "架构师": Willingness(False, None),
        "项目经理": Willingness(False, None),
    }
    rid = pair["room"]["room_id"]
    guest = client.post(f"/rooms/{rid}/join", json={"display_name": "林工"}).json()
    mine = env.queue.enqueue(rid, None, "成员想说", "member", member_id=guest["member_id"])
    env.memory.append_message(rid, SourceType.HUMAN, pair["host_id"], "还有别的角度吗？")

    seen = _run_round(env, rid)

    hands = env.queue.list(rid)
    assert [(h.raise_id, h.intent, h.source) for h in hands] == [
        (mine.raise_id, "成员想说", "member")
    ]
    assert seen == []


def test_the_round_signs_a_member_speaker(client, env, pair):
    env.config.set_auto_raise(True)
    rid = pair["room"]["room_id"]
    guest = client.post(f"/rooms/{rid}/join", json={"display_name": "林工"}).json()
    env.memory.append_message(rid, SourceType.HUMAN, guest["member_id"], "我附议")

    _run_round(env, rid)

    assert env.willing.speakers == ["成员 林工", "成员 林工"]


def test_the_round_signs_the_host_speaker(env, pair):
    env.config.set_auto_raise(True)
    rid = pair["room"]["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, pair["host_id"], "谁有意见？")

    _run_round(env, rid)

    assert env.willing.speakers == ["主持人 陈", "主持人 陈"]


def test_the_round_signs_an_agent_speaker(env, pair):
    env.config.set_auto_raise(True)
    rid = pair["room"]["room_id"]
    env.memory.append_message(rid, SourceType.AGENT, pair["first"], "我先说一句。")

    _run_round(env, rid)

    assert env.willing.speakers == ["架构师"]
    assert env.willing.asked == [pair["second"]]
