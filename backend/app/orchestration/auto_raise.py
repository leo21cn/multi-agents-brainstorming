"""自主举手轮次：一条新发言落定后，重问在席智能体是否有话要说。

轮次是对待发言队列的一次重判，不是追加：已经举手的智能体同样要重新判定——上下文变了，
想说的话可能变了，也可能不想说了。判定结果只改写队列，不触发任何生成，放行权仍在
主持人手里。
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable

from app.domain.enums import RoomStatus, SourceType
from app.orchestration.queue import SOURCE_AUTO

# 单个判定的硬界：提供方自带超时之外再兜一层，线程池卡住时轮次也不会永不退出
JUDGE_TIMEOUT = 20.0

# 一轮内同时进行的判定数：逐个判定会把刷新延迟乘以在席人数，全量并发又会一次吃干
# 线程池与模型配额
JUDGE_CONCURRENCY = 4

# 每房间同时只跑一轮：轮次与发言并发，不合并会让同一批智能体被反复询问
_running: set[str] = set()

# 轮次进行中又有新发言落定的房间：不重开本轮，收尾后补跑一轮，免得刷新被合并丢掉
_again: set[str] = set()


async def raise_round(
    room_id: str,
    *,
    speech,
    queue,
    room_service,
    config,
    willing,
    notify: Callable[[dict], Awaitable[None]],
    reserved: Callable[[], Iterable[str]] = lambda: (),
) -> None:
    """重判在席智能体的发言意愿，把待发言队列刷新成本轮上下文下的样子。

    `reserved` 给出已被主持人安排发言的智能体（正在生成的与待发言队列里的），在挑选
    对象与应用结果时各读一次：判定要花时间，期间主持人可能放行某条举手。
    """
    if not config.get_auto_raise():
        return
    while True:
        if room_id in _running:
            _again.add(room_id)
            return
        room = room_service.get_room(room_id)
        if room is None or room.status != RoomStatus.IN_PROGRESS:
            return
        _running.add(room_id)
        try:
            await _judge_once(
                room_id,
                speech=speech,
                queue=queue,
                room_service=room_service,
                willing=willing,
                notify=notify,
                reserved=reserved,
            )
        finally:
            _running.discard(room_id)
        if room_id not in _again:
            return
        _again.discard(room_id)


def _speaker_label(room_service, speech, room_id: str, last) -> str | None:
    """最新发言的署名：判定要分辨「主持人提问」与「成员附议」，二者含义不同。"""
    if last is None:
        return None
    if last.source_type == SourceType.AGENT:
        agent = speech.config.get_agent(last.source_id)
        return agent.name if agent is not None else last.source_id
    brief = room_service.member_brief(room_id, last.source_id)
    if brief is None:
        # 字面值 "host" 是实施前落库的历史消息
        return "主持人" if last.source_id == "host" else "人类成员"
    name, is_host = brief
    return f"主持人 {name}" if is_host else f"成员 {name}"


async def _judge_once(
    room_id: str,
    *,
    speech,
    queue,
    room_service,
    willing,
    notify: Callable[[dict], Awaitable[None]],
    reserved: Callable[[], Iterable[str]],
) -> None:
    msgs = speech.memory.all_messages(room_id)
    last = msgs[-1] if msgs else None
    text = last.content if last is not None else ""
    label = _speaker_label(room_service, speech, room_id, last)
    # 发言人由最后一条消息认定：智能体不必对自己刚说的话表态
    speaker = (
        last.source_id
        if last is not None and last.source_type == SourceType.AGENT
        else None
    )
    busy = set(reserved())
    targets: list[tuple[str, object, object]] = []
    for aid in room_service.selected_agent_ids(room_id):
        if aid == speaker or aid in busy:
            continue
        hand = queue.find(room_id, aid)
        # 主持人代填的举手是主持人的意思，判定无权改写或撤回
        if hand is not None and hand.source != SOURCE_AUTO:
            continue
        agent = speech.config.get_agent(aid)
        if agent is None:
            continue
        targets.append((aid, hand, agent))
    if not targets:
        return

    results = await _judge_all(willing, targets, text, label)

    # 判定要花时间，期间房间可能已结束
    fresh = room_service.get_room(room_id)
    if fresh is None or fresh.status != RoomStatus.IN_PROGRESS:
        return
    busy = set(reserved())
    changed = False
    for (aid, hand, _agent), res in zip(targets, results):
        # 判定失败一律保留原样：既不新增举手，也不把已有举手撤回
        if res is None or aid in busy:
            continue
        current = queue.find(room_id, aid)
        if hand is not None and current is None:
            # 本轮期间主持人已放行或驳回这条举手，不再替他做决定
            continue
        if current is None:
            if not res.want:
                continue
            queue.enqueue(room_id, aid, res.intent, source=SOURCE_AUTO)
            changed = True
        elif current.source != SOURCE_AUTO:
            continue
        elif not res.want:
            queue.withdraw(room_id, current.raise_id)
            changed = True
        elif current.intent != res.intent:
            # 保留位置与标识，只改写想说的话：主持人不该看到队伍因刷新而重排
            queue.refresh(room_id, current.raise_id, res.intent)
            changed = True
    if changed:
        await notify({"type": "raise_hand_update"})


async def _judge_all(willing, targets, text: str, speaker: str | None) -> list:
    """并发发起全部判定，按阵容次序返回：完成的先后不该决定队列次序。"""
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)

    async def one(agent):
        async with sem:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(willing.check, agent, text, speaker), JUDGE_TIMEOUT
                )
            except Exception:
                # 超时与异常都记为「没有结论」，由应用阶段保留原样
                return None

    return list(await asyncio.gather(*(one(agent) for _aid, _h, agent in targets)))
