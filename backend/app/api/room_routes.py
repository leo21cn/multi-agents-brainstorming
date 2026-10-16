import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.api.identity import NO_FLOOR, member_credentials, require_host_member, require_member
from app.api.schemas import (
    ClaimRequest,
    HostAssign,
    HumanMessageIn,
    JoinRequest,
    RaiseHandRequest,
    RoomCreate,
    SelfRaise,
    SpeakRequest,
)
from app.api.ws import manager
from app.deps import (
    get_asr_provider,
    get_conclusion_gate,
    get_config_service,
    get_conclusion_service,
    get_floor,
    get_memory_service,
    get_raise_hand_queue,
    get_room_service,
    get_speech_service,
)
from app.domain.enums import MemberStatus, RoomStatus, SourceType
from app.models.asr import AsrError
from app.orchestration.conclusion_gate import CONCLUDING_DETAIL
from app.orchestration.floor import address_grants
from app.orchestration.queue import SOURCE_MEMBER
from app.services.conclusion_service import EmptyMemoryError, SynthesisError
from app.services.room_service import JoinRefused, RoomNotFound, RoomService
from app.services.speech_service import RoomClosedError, SpeechError
from app.util import new_id, token_matches

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _member_out(member) -> dict:
    # 令牌只在发放那一刻随响应出现一次；成员列表与其余任何输出都不带它
    return member.model_dump(exclude={"token"})


@router.post("", status_code=201)
def create_room(body: RoomCreate, svc: RoomService = Depends(get_room_service)):
    # 命题最多 200 字：标题栏不放命题后，命题在聊天窗口上方置顶完整展示，须给长度上限
    if len(body.proposition.strip()) > 200:
        raise HTTPException(status_code=422, detail="命题过长，请控制在 200 字以内")
    try:
        room, host_token = svc.create_room(
            body.proposition, body.orchestration_mode, body.agent_ids, body.host_name
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {**room.model_dump(), "host_token": host_token}


@router.get("")
def list_rooms(svc: RoomService = Depends(get_room_service)):
    return [r.model_dump() for r in svc.list_rooms()]


@router.get("/{room_id}")
def get_room(
    room_id: str,
    svc: RoomService = Depends(get_room_service),
    floor=Depends(get_floor),
):
    room = svc.get_room(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return {
        **room.model_dump(),
        "agent_ids": svc.selected_agent_ids(room_id),
        # 发言权随房间一起下发：刷新与重连后界面才知道谁正被放行
        "floor_member_ids": floor.holders(room_id),
    }


@router.post("/{room_id}/join", status_code=201)
async def join_room(
    room_id: str, body: JoinRequest, svc: RoomService = Depends(get_room_service)
):
    """受邀者自助进房：登记昵称、发放令牌，令牌只在此处返回一次。"""
    try:
        member, token = svc.join_room(room_id, body.display_name)
    except RoomNotFound:
        raise HTTPException(status_code=404, detail="room not found")
    except JoinRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await manager.broadcast(room_id, {"type": "member_update"})
    return {**_member_out(member), "token": token}


@router.post("/{room_id}/claim")
def claim_member(
    room_id: str,
    body: ClaimRequest,
    svc: RoomService = Depends(get_room_service),
    config=Depends(get_config_service),
):
    """凭管理令牌为既有成员重新发放令牌；入口在进房落地页，管理令牌经请求体传入而非
    存进前端状态，免得它随后续每个请求一起发出去。"""
    if not token_matches(body.admin_token, config.get_admin_token()):
        raise HTTPException(status_code=403, detail="invalid admin token")
    try:
        member, token = svc.reissue_token(room_id, body.member_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {**_member_out(member), "token": token}


@router.get("/{room_id}/members")
def list_members(room_id: str, svc: RoomService = Depends(get_room_service)):
    return [_member_out(m) for m in svc.members(room_id)]


@router.post("/{room_id}/leave")
async def leave_room(
    room_id: str,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    floor=Depends(get_floor),
    queue=Depends(get_raise_hand_queue),
):
    """退出聊天室（服务端动作）：标记已退出并清令牌，清发言权与排队举手。

    来源取自连接身份，请求体里没有可填的成员标识。主持人在房间未结束时须先移交或
    先结束——由服务层强制，故不会留下「主持人是已退出成员」的房间。
    """
    member = require_member(room_id, svc, creds)
    try:
        svc.leave_room(room_id, member.member_id)
    except RoomNotFound:
        raise HTTPException(status_code=404, detail="room not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    # 内存态清理：退出者不该再留发言权，也不该再排队举手
    had_floor = floor.holds(room_id, member.member_id)
    floor.revoke(room_id, member.member_id)
    hand = queue.find_member(room_id, member.member_id)
    if hand is not None:
        queue.withdraw(room_id, hand.raise_id)
    await manager.broadcast(room_id, {"type": "member_update"})
    if hand is not None:
        await manager.broadcast(room_id, {"type": "raise_hand_update"})
    if had_floor:
        await manager.broadcast(
            room_id,
            {"type": "floor_update", "member_ids": floor.holders(room_id), "granted": []},
        )
    return {"ok": True}


@router.post("/{room_id}/start")
async def start_room(
    room_id: str,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
):
    require_host_member(room_id, svc, creds)
    room = await asyncio.to_thread(svc.start_room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    await manager.broadcast(
        room_id, {"type": "room_state", "room": room.model_dump(mode="json")}
    )
    return room.model_dump()


@router.post("/{room_id}/host")
async def assign_host(
    room_id: str,
    body: HostAssign,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
):
    require_host_member(room_id, svc, creds)
    try:
        room = await asyncio.to_thread(svc.set_host, room_id, body.member_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    await manager.broadcast(room_id, {"type": "member_update"})
    return room.model_dump()


@router.post("/{room_id}/end")
async def end_room(
    room_id: str,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    floor=Depends(get_floor),
):
    require_host_member(room_id, svc, creds)
    room = await asyncio.to_thread(svc.end_room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    # 发言权与举手都不该跨房间结束存活：留着就是永不兑现的承诺
    floor.clear(room_id)
    await manager.broadcast(
        room_id, {"type": "room_state", "room": room.model_dump(mode="json")}
    )
    return room.model_dump()


# ---------- 讨论：消息、点名、举手 ----------
@router.post("/{room_id}/messages", status_code=201)
async def post_human_message(
    room_id: str,
    body: HumanMessageIn,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    memory=Depends(get_memory_service),
    floor=Depends(get_floor),
    queue=Depends(get_raise_hand_queue),
    gate=Depends(get_conclusion_gate),
):
    member = require_member(room_id, svc, creds)
    room = svc.get_room(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    if room.status == RoomStatus.ENDED:
        raise HTTPException(status_code=422, detail="房间已结束，不再新增发言")
    if gate.active(room_id):
        # 阶段结论生成期间暂停发言：与实时通道同口径，新发言一律拒绝
        raise HTTPException(status_code=422, detail=CONCLUDING_DETAIL)
    if not member.is_host and not floor.holds(room_id, member.member_id):
        raise HTTPException(status_code=403, detail=NO_FLOOR)
    targets = list(dict.fromkeys(body.addressee_agent_ids or []))
    roster = svc.selected_agent_ids(room_id)
    # 任一受话者不在阵容内则整条拒绝：半接受会让落库消息的收件人与原文里的 @ 名字对不上
    if any(t not in roster for t in targets):
        raise HTTPException(status_code=422, detail="受话者不在房间阵容内")
    people = list(dict.fromkeys(body.addressee_member_ids or []))
    by_id = {m.member_id: m for m in svc.members(room_id)}
    if any(t not in by_id for t in people):
        raise HTTPException(status_code=422, detail="受话者不在房间内")
    for t in people:
        if by_id[t].status == MemberStatus.LEFT:
            # @ 已退出成员：整条拒绝，不落库、不触发生成
            raise HTTPException(
                status_code=422, detail=f"{by_id[t].display_name} 已退出聊天室，不在线，消息未发送"
            )
    msg = memory.append_message(
        room_id,
        SourceType.HUMAN,
        member.member_id,
        body.content,
        addressee_agent_ids=targets,
        addressee_member_ids=people,
    )
    # 降级通道同样广播：成员与主持人可能各自只有一条通道可用，静默会让在线的那些看不到这条发言
    await manager.broadcast(
        room_id, {"type": "message", "message": msg.model_dump(mode="json")}
    )
    floor.consume(room_id, member.member_id)
    granted = address_grants(floor, queue, room_id, member, people)
    if granted:
        await manager.broadcast(room_id, {"type": "raise_hand_update"})
        await manager.broadcast(
            room_id,
            {
                "type": "floor_update",
                "member_ids": floor.holders(room_id),
                "granted": granted,
            },
        )
    return msg.model_dump()


@router.get("/{room_id}/messages")
def list_messages(room_id: str, memory=Depends(get_memory_service)):
    return [m.model_dump() for m in memory.all_messages(room_id)]


@router.post("/{room_id}/speak", status_code=201)
def speak(
    room_id: str,
    body: SpeakRequest,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    speech=Depends(get_speech_service),
    gate=Depends(get_conclusion_gate),
):
    require_host_member(room_id, svc, creds)
    if gate.active(room_id):
        raise HTTPException(status_code=422, detail=CONCLUDING_DETAIL)
    try:
        msg = speech.generate(room_id, body.agent_id, body.intent, body.directed_from)
    except RoomClosedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return msg.model_dump()


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{room_id}/speak-stream")
def speak_stream(
    room_id: str,
    body: SpeakRequest,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    speech=Depends(get_speech_service),
    gate=Depends(get_conclusion_gate),
):
    """流式发言（WebSocket 不可用时的降级通道）。

    同步生成器由 Starlette 线程池迭代，不阻塞事件循环。
    """
    require_host_member(room_id, svc, creds)
    room = svc.get_room(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    if room.status == RoomStatus.ENDED:
        raise HTTPException(status_code=422, detail="房间已结束，不再新增发言")
    if gate.active(room_id):
        raise HTTPException(status_code=422, detail=CONCLUDING_DETAIL)

    def events():
        stream_id = new_id("stream")
        head = {"stream_id": stream_id, "agent_id": body.agent_id}
        try:
            for kind, payload in speech.generate_stream(
                room_id, body.agent_id, body.intent, body.directed_from
            ):
                if kind == "stage":
                    yield _sse("stage", {**head, "stage": payload})
                elif kind == "thought":
                    yield _sse("thought", {**head, "delta": payload})
                elif kind == "delta":
                    yield _sse("delta", {**head, "delta": payload})
                elif kind == "message":
                    yield _sse(
                        "message",
                        {
                            **head,
                            "message": payload.model_dump(mode="json"),
                            "truncated": bool(payload.truncated),
                        },
                    )
        except Exception as exc:
            # 响应已开始，无法再改状态码；任何异常都须收束为 error 事件，否则客户端只看到被截断的响应
            yield _sse("error", {**head, "detail": str(exc) or type(exc).__name__})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/{room_id}/raise-hand", status_code=201)
async def raise_hand(
    room_id: str,
    body: RaiseHandRequest,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    queue=Depends(get_raise_hand_queue),
):
    require_host_member(room_id, svc, creds)
    item = queue.enqueue(room_id, body.agent_id, body.intent)
    # 降级通道同样广播：实时通道可能只对一部分客户端可用，静默会让在线的那些停在旧队列上
    await manager.broadcast(room_id, {"type": "raise_hand_update"})
    return {
        "raise_id": item.raise_id,
        "agent_id": item.agent_id,
        "intent": item.intent,
        "status": item.status.value,
    }


@router.get("/{room_id}/raise-hand")
def list_raise_hand(room_id: str, queue=Depends(get_raise_hand_queue)):
    return [
        {
            "raise_id": i.raise_id,
            "agent_id": i.agent_id,
            "member_id": i.member_id,
            "intent": i.intent,
            "source": i.source,
        }
        for i in queue.list(room_id)
    ]


@router.post("/{room_id}/raise-hand/self", status_code=201)
async def raise_self(
    room_id: str,
    body: SelfRaise,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    queue=Depends(get_raise_hand_queue),
):
    """成员为自己举手：主持人的入口是 `raise-hand`（代填智能体），二者分开。"""
    member = require_member(room_id, svc, creds)
    if member.is_host:
        raise HTTPException(status_code=403, detail="主持人有话直接说，不必举手")
    existing = queue.find_member(room_id, member.member_id)
    if existing is None:
        item = queue.enqueue(
            room_id, None, body.intent, SOURCE_MEMBER, member_id=member.member_id
        )
    else:
        queue.refresh(room_id, existing.raise_id, body.intent)
        item = existing
    await manager.broadcast(room_id, {"type": "raise_hand_update"})
    return {
        "raise_id": item.raise_id,
        "agent_id": item.agent_id,
        "member_id": item.member_id,
        "intent": item.intent,
        "source": item.source,
        "status": item.status.value,
    }


@router.post("/{room_id}/raise-hand/{raise_id}/withdraw")
async def withdraw_hand(
    room_id: str,
    raise_id: str,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    queue=Depends(get_raise_hand_queue),
):
    member = require_member(room_id, svc, creds)
    item = queue.get(room_id, raise_id)
    if item is not None and (item.member_id == member.member_id or member.is_host):
        queue.withdraw(room_id, raise_id)
    await manager.broadcast(room_id, {"type": "raise_hand_update"})
    return {"ok": True}


@router.post("/{room_id}/raise-hand/{raise_id}/release", status_code=201)
async def release_hand(
    room_id: str,
    raise_id: str,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    queue=Depends(get_raise_hand_queue),
    speech=Depends(get_speech_service),
    floor=Depends(get_floor),
    gate=Depends(get_conclusion_gate),
):
    require_host_member(room_id, svc, creds)
    if gate.active(room_id):
        raise HTTPException(status_code=422, detail=CONCLUDING_DETAIL)
    items = {i.raise_id: i for i in queue.list(room_id)}
    if raise_id not in items:
        raise HTTPException(status_code=404, detail="raise hand not found")
    item = items[raise_id]
    queue.release(room_id, raise_id)
    await manager.broadcast(room_id, {"type": "raise_hand_update"})
    if item.member_id is not None:
        # 放行人类举手不触发生成：授予一次发言权，由他自己开口
        floor.grant(room_id, item.member_id)
        await manager.broadcast(
            room_id,
            {
                "type": "floor_update",
                "member_ids": floor.holders(room_id),
                "granted": [{"member_id": item.member_id, "reason": "released"}],
            },
        )
        return {
            "released": "member",
            "member_id": item.member_id,
            "floor_member_ids": floor.holders(room_id),
        }
    try:
        # 端点是 async 的，同步的模型调用交给工作线程，免得阻塞事件循环
        msg = await asyncio.to_thread(speech.generate, room_id, item.agent_id, item.intent)
    except RoomClosedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"released": "agent", "message": msg.model_dump()}


@router.post("/{room_id}/raise-hand/{raise_id}/dismiss")
async def dismiss_hand(
    room_id: str,
    raise_id: str,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    queue=Depends(get_raise_hand_queue),
):
    require_host_member(room_id, svc, creds)
    queue.dismiss(room_id, raise_id)
    await manager.broadcast(room_id, {"type": "raise_hand_update"})
    return {"ok": True}


@router.post("/{room_id}/asr")
async def transcribe_audio(
    room_id: str,
    request: Request,
    creds=Depends(member_credentials),
    svc: RoomService = Depends(get_room_service),
    config=Depends(get_config_service),
    provider=Depends(get_asr_provider),
):
    """语音转写：成员录音（16kHz 单声道 s16le PCM）上传，转写文本直接返回。

    只对房内成员开放（转写消耗模型配额）；音频仅在内存中处理，不落盘、不入日志。
    转写为同步阻塞调用，经工作线程执行。
    """
    require_member(room_id, svc, creds)
    conn_id, model = config.get_asr_model()
    if not conn_id or not model:
        raise HTTPException(status_code=422, detail="尚未配置语音转写模型，请先在管理后台设置")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="录音为空")
    if len(body) > 2_000_000:
        raise HTTPException(status_code=422, detail="录音过长（最长 60 秒）")
    try:
        text = await asyncio.to_thread(provider.transcribe, body)
    except AsrError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"text": text}


# ---------- 结论与导出 ----------
@router.post("/{room_id}/conclusion", status_code=201)
async def conclusion(
    room_id: str,
    creds=Depends(member_credentials),
    room_svc: RoomService = Depends(get_room_service),
    svc=Depends(get_conclusion_service),
    gate=Depends(get_conclusion_gate),
):
    """生成阶段结论：先广播全房间的生成中提示，完成后广播结果。

    生成期间暂停发言——发言触发在 REST 与实时通道都读同一把门闩，新发言一律被拒。
    生成是长达 180 秒的阻塞调用，交给工作线程，事件循环保持可响应；开始与完成两个
    事件都向房间广播，所有客户端据此呈现等待模态并在成功后自动打开产出模态。
    """
    require_host_member(room_id, room_svc, creds)
    if not gate.begin(room_id):
        raise HTTPException(status_code=422, detail=CONCLUDING_DETAIL)
    await manager.broadcast(room_id, {"type": "conclusion_started"})
    try:
        try:
            doc = await asyncio.to_thread(svc.generate, room_id)
        except EmptyMemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except SynthesisError as exc:
            # 综合失败不写半截文档：与发言失败同口径拒绝，主持人重试即可
            raise HTTPException(status_code=502, detail=str(exc))
    except HTTPException:
        await manager.broadcast(room_id, {"type": "conclusion_done", "ok": False})
        raise
    except Exception:
        # 未知异常同样收束为失败完成：等待模态必须被关掉，否则客户端停在生成中
        await manager.broadcast(room_id, {"type": "conclusion_done", "ok": False})
        raise
    finally:
        gate.end(room_id)
    await manager.broadcast(room_id, {"type": "conclusion_done", "ok": True})
    return doc.model_dump()


@router.get("/{room_id}/conclusion")
def latest_conclusion(room_id: str, svc=Depends(get_conclusion_service)):
    doc = svc.latest_conclusion(room_id)
    return doc.model_dump() if doc else None


@router.get("/{room_id}/conclusion/{doc_id}/download")
def download_conclusion(room_id: str, doc_id: str, svc=Depends(get_conclusion_service)):
    doc = svc.get_conclusion(doc_id)
    if doc is None or doc.room_id != room_id:
        raise HTTPException(status_code=404, detail="conclusion not found")
    path = svc.resolve_artifact(doc.file_ref)
    if path is None:
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(str(path), media_type="text/markdown", filename=path.name)


@router.post("/{room_id}/export", status_code=201)
def export_transcript(
    room_id: str,
    creds=Depends(member_credentials),
    room_svc: RoomService = Depends(get_room_service),
    svc=Depends(get_conclusion_service),
):
    require_host_member(room_id, room_svc, creds)
    exp = svc.export_transcript(room_id)
    return exp.model_dump()


@router.get("/{room_id}/export")
def latest_export(room_id: str, svc=Depends(get_conclusion_service)):
    exp = svc.latest_export(room_id)
    return exp.model_dump() if exp else None


@router.get("/{room_id}/export/{export_id}/download")
def download_export(room_id: str, export_id: str, svc=Depends(get_conclusion_service)):
    exp = svc.get_export(export_id)
    if exp is None or exp.room_id != room_id:
        raise HTTPException(status_code=404, detail="export not found")
    path = svc.resolve_artifact(exp.file_ref)
    if path is None:
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(str(path), media_type="text/plain", filename=path.name)
