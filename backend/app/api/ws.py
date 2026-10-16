import asyncio
import base64
from dataclasses import dataclass

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.identity import IDENTITY_INVALID, NO_FLOOR, NOT_HOST, resolve_member
from app.deps import (
    get_asr_provider,
    get_conclusion_gate,
    get_config_service,
    get_floor,
    get_raise_hand_queue,
    get_room_service,
    get_speech_service,
    get_willingness_service,
)
from app.domain.enums import MemberStatus, RoomStatus, SourceType
from app.models.asr import AsrError
from app.orchestration.auto_raise import raise_round
from app.orchestration.conclusion_gate import CONCLUDING_DETAIL
from app.orchestration.floor import address_grants
from app.orchestration.queue import SOURCE_MEMBER
from app.util import new_id

router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self._rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, room_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms.setdefault(room_id, []).append(ws)

    def disconnect(self, room_id: str, ws: WebSocket) -> None:
        self._rooms[room_id] = [w for w in self._rooms.get(room_id, []) if w is not ws]

    async def broadcast(self, room_id: str, event: dict) -> None:
        for ws in list(self._rooms.get(room_id, [])):
            try:
                await ws.send_json(event)
            except Exception:
                # 客户端可能在生成期间断开；不应中断对其余连接的广播
                self.disconnect(room_id, ws)


manager = ConnectionManager()

# 每房间同时只允许一次生成：主持人控制下不存在并发发言。忙时不拒绝、不丢弃，
# 一律排入本房间 FIFO，前一次收束后依次触发。值为正在发言的智能体——判定轮次要
# 把他排除在外：他的发言还没落库，上下文里还没有他自己。
_inflight: dict[str, str] = {}
_pending: dict[str, list["PendingSpeech"]] = {}
_DONE = object()


@dataclass
class PendingSpeech:
    """一次待触发的发言：多受话者按书写次序各占一条，点名与放行举手各占一条。"""

    agent_id: str
    intent: str | None = None
    directed_from: str | None = None


async def stream_speech(
    room_id: str,
    agent_id: str,
    intent: str | None,
    speech,
    directed_from: str | None = None,
    on_done=None,
) -> None:
    """在线程中迭代同步生成器，逐段广播阶段、增量与最终消息。

    单人发言守卫由 pump 在派发前认领，本函数不再自行判定忙闲。on_done 在终结事件广播
    之后调用，故客户端收到终结即可立刻发起下一次点名，不会被误判为并发。
    """
    stream_id = new_id("stream")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker() -> None:
        try:
            for item in speech.generate_stream(room_id, agent_id, intent, directed_from):
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:  # 含 SpeechError：模型超时、智能体缺失等
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    terminal: dict | None = None
    try:
        worker_task = asyncio.create_task(asyncio.to_thread(worker))
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            kind, payload = item
            if kind == "stage":
                await manager.broadcast(
                    room_id,
                    {
                        "type": "stage",
                        "agent_id": agent_id,
                        "stream_id": stream_id,
                        "stage": payload,
                    },
                )
            elif kind == "thought":
                await manager.broadcast(
                    room_id,
                    {
                        "type": "thought_delta",
                        "agent_id": agent_id,
                        "stream_id": stream_id,
                        "delta": payload,
                    },
                )
            elif kind == "delta":
                await manager.broadcast(
                    room_id,
                    {
                        "type": "message_delta",
                        "agent_id": agent_id,
                        "stream_id": stream_id,
                        "delta": payload,
                    },
                )
            elif kind == "message":
                # 带上 stream_id：前端据此把流式气泡原位替换为落库消息
                terminal = {
                    "type": "message",
                    "stream_id": stream_id,
                    "message": payload.model_dump(mode="json"),
                    "truncated": bool(payload.truncated),
                }
            elif kind == "error":
                terminal = {"type": "speech_failed", "detail": payload}
        await worker_task
    finally:
        _inflight.pop(room_id, None)
    # 终结事件在守卫释放后广播：客户端收到即可立刻发起下一次点名，不会被误判为并发
    if terminal is not None:
        await manager.broadcast(room_id, terminal)
    if on_done is not None:
        await on_done()


@router.websocket("/rooms/{room_id}/ws")
async def room_ws(
    ws: WebSocket,
    room_id: str,
    speech=Depends(get_speech_service),
    room_service=Depends(get_room_service),
    queue=Depends(get_raise_hand_queue),
    config=Depends(get_config_service),
    willing=Depends(get_willingness_service),
    floor=Depends(get_floor),
    gate=Depends(get_conclusion_gate),
    asr=Depends(get_asr_provider),
):
    member = resolve_member(
        room_service, room_id, ws.query_params.get("member_id"), ws.query_params.get("token")
    )
    if member is None:
        # 未 accept 即可关闭：校验不过的连接不该进入接收循环，也不该收到任何房间事件
        await ws.close(code=1008, reason=IDENTITY_INVALID)
        return
    await manager.connect(room_id, ws)

    def pump() -> None:
        """认领并派发队首：认领与派发之间没有 await，故同一条不会被认领两次。"""
        if room_id in _inflight:
            return
        room = room_service.get_room(room_id)
        if room is None or room.status == RoomStatus.ENDED:
            # 已结束房间里排队的发言一律作废，否则会连播一串「房间已结束」失败提示
            _pending.pop(room_id, None)
            return
        items = _pending.get(room_id)
        if not items:
            return
        nxt = items.pop(0)
        _inflight[room_id] = nxt.agent_id
        asyncio.create_task(
            stream_speech(
                room_id,
                nxt.agent_id,
                nxt.intent,
                speech,
                directed_from=nxt.directed_from,
                on_done=settle,
            )
        )

    def reserved() -> list[str]:
        """已被主持人安排发言的智能体：正在生成的与待发言队列里的。"""
        speaking = _inflight.get(room_id)
        pending = [p.agent_id for p in _pending.get(room_id, [])]
        return pending + ([speaking] if speaking else [])

    async def refuse(detail: str) -> None:
        await ws.send_json({"type": "rejected", "detail": detail})

    async def announce_floor(granted: list[dict]) -> None:
        """把发言权的当前样子整体下发：持有者据此解锁输入框，其余人据此标注成员面板。"""
        await manager.broadcast(
            room_id,
            {
                "type": "floor_update",
                "member_ids": floor.holders(room_id),
                "granted": granted,
            },
        )

    async def settle() -> None:
        """一条发言或人类消息落定后的串行钩子：先泵队列，再刷新举手。

        泵在前是刻意的：判定结果只进入待发言队列、由主持人放行，不会自行发言，故先泵
        不会让新举手插到已排队的应答之前；反之多受话者的第二条应答会被判定耗时推迟。
        占用名单由 reserved 在挑选对象与应用结果时各读一次，故泵后再取也不会漏掉刚派发
        的那个人。
        """
        pump()
        await raise_round(
            room_id,
            speech=speech,
            queue=queue,
            room_service=room_service,
            config=config,
            willing=willing,
            notify=lambda event: manager.broadcast(room_id, event),
            reserved=reserved,
        )

    try:
        asr_session = None
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            if kind == "point":
                if not member.is_host:
                    await refuse(NOT_HOST)
                    continue
                if gate.active(room_id):
                    # 结论生成期间暂停发言：与 REST 同口径，新发言一律拒绝
                    await refuse(CONCLUDING_DETAIL)
                    continue
                # 派发后立刻回到接收循环，生成期间本连接仍可处理其他上行指令
                _pending.setdefault(room_id, []).append(
                    PendingSpeech(data["agent_id"], data.get("intent"))
                )
                pump()
            elif kind == "raise_hand":
                if not member.is_host:
                    await refuse(NOT_HOST)
                    continue
                item = queue.enqueue(room_id, data["agent_id"], data.get("intent"))
                await manager.broadcast(
                    room_id,
                    {
                        "type": "raise_hand",
                        "agent_id": item.agent_id,
                        "raise_id": item.raise_id,
                        "source": item.source,
                    },
                )
            elif kind == "release":
                if not member.is_host:
                    await refuse(NOT_HOST)
                    continue
                if gate.active(room_id):
                    await refuse(CONCLUDING_DETAIL)
                    continue
                items = {i.raise_id: i for i in queue.list(room_id)}
                item = items.get(data.get("raise_id"))
                if item is None:
                    # 举手可能已随讨论推进被智能体自己撤回：主持人点的是过期条目，
                    # 不给回执就会让界面停在一个永远不会兑现的占位上
                    await ws.send_json(
                        {
                            "type": "rejected",
                            "detail": "举手已不在队列中，可能已随讨论推进被撤回",
                        }
                    )
                    await manager.broadcast(room_id, {"type": "raise_hand_update"})
                else:
                    queue.release(room_id, item.raise_id)
                    await manager.broadcast(room_id, {"type": "raise_hand_update"})
                    if item.member_id is not None:
                        # 放行人类举手不触发生成：授予一次发言权，由他自己开口
                        floor.grant(room_id, item.member_id)
                        await announce_floor(
                            [{"member_id": item.member_id, "reason": "released"}]
                        )
                    else:
                        _pending.setdefault(room_id, []).append(
                            PendingSpeech(item.agent_id, item.intent)
                        )
                        pump()
            elif kind == "dismiss":
                if not member.is_host:
                    await refuse(NOT_HOST)
                    continue
                queue.dismiss(room_id, data.get("raise_id", ""))
                await manager.broadcast(room_id, {"type": "raise_hand_update"})
            elif kind == "raise_self":
                # 主体取自连接身份，故载荷里没有标识：成员无法替别人举手
                mine = queue.find_member(room_id, member.member_id)
                if mine is None:
                    queue.enqueue(
                        room_id,
                        None,
                        data.get("intent"),
                        SOURCE_MEMBER,
                        member_id=member.member_id,
                    )
                else:
                    # 上下文变了、想说的话也变了：改写意图，保留位置与标识
                    queue.refresh(room_id, mine.raise_id, data.get("intent"))
                await manager.broadcast(room_id, {"type": "raise_hand_update"})
            elif kind == "withdraw_hand":
                item = queue.get(room_id, data.get("raise_id", ""))
                if item is not None and (
                    item.member_id == member.member_id or member.is_host
                ):
                    queue.withdraw(room_id, item.raise_id)
                await manager.broadcast(room_id, {"type": "raise_hand_update"})
            elif kind == "human_message":
                room = room_service.get_room(room_id)
                if room is None:
                    await ws.send_json({"type": "rejected", "detail": "room not found"})
                    continue
                if room.status == RoomStatus.ENDED:
                    await ws.send_json(
                        {"type": "rejected", "detail": "房间已结束，不再新增发言"}
                    )
                    continue
                if gate.active(room_id):
                    await refuse(CONCLUDING_DETAIL)
                    continue
                if not member.is_host and not floor.holds(room_id, member.member_id):
                    await refuse(NO_FLOOR)
                    continue
                targets = list(dict.fromkeys(data.get("addressee_agent_ids") or []))
                roster = room_service.selected_agent_ids(room_id)
                if any(t not in roster for t in targets):
                    await ws.send_json(
                        {"type": "rejected", "detail": "受话者不在房间阵容内"}
                    )
                    continue
                people = list(dict.fromkeys(data.get("addressee_member_ids") or []))
                by_id = {m.member_id: m for m in room_service.members(room_id)}
                if any(t not in by_id for t in people):
                    await refuse("受话者不在房间内")
                    continue
                left = [by_id[t].display_name for t in people if by_id[t].status == MemberStatus.LEFT]
                if left:
                    # @ 已退出成员：整条拒绝，与 REST 同口径
                    await refuse(f"{'、'.join(left)} 已退出聊天室，不在线，消息未发送")
                    continue
                msg = speech.memory.append_message(
                    room_id,
                    SourceType.HUMAN,
                    member.member_id,
                    data.get("content", ""),
                    addressee_agent_ids=targets,
                    addressee_member_ids=people,
                )
                await manager.broadcast(
                    room_id, {"type": "message", "message": msg.model_dump(mode="json")}
                )
                floor.consume(room_id, member.member_id)
                granted = address_grants(floor, queue, room_id, member, people)
                if granted:
                    await manager.broadcast(room_id, {"type": "raise_hand_update"})
                    await announce_floor(granted)
                # 定向提问视同点名：多个受话者依次应答，各自锚定同一条提问消息。
                # 只对主持人的消息成立——成员的 @ 只作标注，触发应答要经放行。
                if member.is_host:
                    _pending.setdefault(room_id, []).extend(
                        PendingSpeech(t, msg.content, msg.message_id) for t in targets
                    )
                asyncio.create_task(settle())
            elif kind == "room_state":
                room = room_service.get_room(room_id)
                await ws.send_json(
                    {"type": "room_state", "room": room.model_dump(mode="json") if room else None}
                )
            elif kind == "asr_begin":
                if asr_session is not None:
                    await ws.send_json({"type": "asr_error", "message": "语音会话已在进行中"})
                    continue
                conn_id, model = config.get_asr_model()
                if not conn_id or not model:
                    await ws.send_json(
                        {"type": "asr_error", "message": "尚未配置语音转写模型，请先在管理后台设置"}
                    )
                    continue
                async def on_text(text: str, partial: bool) -> None:
                    try:
                        await ws.send_json({"type": "asr_text", "text": text, "partial": partial})
                    except Exception:
                        pass

                asr_session = asr.open_stream(on_text)
                try:
                    await asr_session.open()
                except AsrError as exc:
                    asr_session = None
                    await ws.send_json({"type": "asr_error", "message": str(exc)})
            elif kind == "asr_audio":
                if asr_session is None:
                    continue
                try:
                    raw = base64.b64decode(data.get("data") or "")
                except Exception:
                    continue
                if not raw or len(raw) > 64 * 1024:
                    continue
                try:
                    await asr_session.send_frame(raw)
                except AsrError as exc:
                    await ws.send_json({"type": "asr_error", "message": str(exc)})
                    await asr_session.abort()
                    asr_session = None
            elif kind == "asr_end":
                if asr_session is None:
                    # 会话可能已随重连后的新连接不复存在：给回执，客户端据此收掉转写态
                    await ws.send_json(
                        {"type": "asr_error", "message": "语音会话已结束或未开启，请重试"}
                    )
                    continue
                sess, asr_session = asr_session, None
                try:
                    final = await sess.finish()
                    await ws.send_json({"type": "asr_done", "text": final})
                except AsrError as exc:
                    await ws.send_json({"type": "asr_error", "message": str(exc)})
                finally:
                    await sess.abort()
    except WebSocketDisconnect:
        if asr_session is not None:
            await asr_session.abort()
            asr_session = None
        manager.disconnect(room_id, ws)
