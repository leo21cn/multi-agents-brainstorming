from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import HumanMessageIn, MemberCreate, RaiseHandRequest, RoomCreate, SpeakRequest
from app.deps import (
    get_conclusion_service,
    get_memory_service,
    get_raise_hand_queue,
    get_room_service,
    get_speech_service,
)
from app.domain.enums import SourceType
from app.services.conclusion_service import EmptyMemoryError
from app.services.room_service import RoomService
from app.services.speech_service import SpeechError

router = APIRouter(prefix="/rooms", tags=["rooms"])


@router.post("", status_code=201)
def create_room(body: RoomCreate, svc: RoomService = Depends(get_room_service)):
    try:
        room = svc.create_room(
            body.proposition, body.orchestration_mode, body.agent_ids, body.host_name
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return room.model_dump()


@router.get("")
def list_rooms(svc: RoomService = Depends(get_room_service)):
    return [r.model_dump() for r in svc.list_rooms()]


@router.get("/{room_id}")
def get_room(room_id: str, svc: RoomService = Depends(get_room_service)):
    room = svc.get_room(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return {**room.model_dump(), "agent_ids": svc.selected_agent_ids(room_id)}


@router.post("/{room_id}/members", status_code=201)
def add_member(room_id: str, body: MemberCreate, svc: RoomService = Depends(get_room_service)):
    try:
        member = svc.add_member(room_id, body.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return member.model_dump()


@router.post("/{room_id}/end")
def end_room(room_id: str, svc: RoomService = Depends(get_room_service)):
    room = svc.end_room(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    return room.model_dump()


# ---------- 讨论：消息、点名、举手 ----------
@router.post("/{room_id}/messages", status_code=201)
def post_human_message(
    room_id: str,
    body: HumanMessageIn,
    svc: RoomService = Depends(get_room_service),
    memory=Depends(get_memory_service),
):
    if svc.get_room(room_id) is None:
        raise HTTPException(status_code=404, detail="room not found")
    msg = memory.append_message(room_id, SourceType.HUMAN, "host", body.content)
    return msg.model_dump()


@router.get("/{room_id}/messages")
def list_messages(room_id: str, memory=Depends(get_memory_service)):
    return [m.model_dump() for m in memory.all_messages(room_id)]


@router.post("/{room_id}/speak", status_code=201)
def speak(room_id: str, body: SpeakRequest, speech=Depends(get_speech_service)):
    try:
        msg = speech.generate(room_id, body.agent_id, body.intent)
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return msg.model_dump()


@router.post("/{room_id}/raise-hand", status_code=201)
def raise_hand(room_id: str, body: RaiseHandRequest, queue=Depends(get_raise_hand_queue)):
    item = queue.enqueue(room_id, body.agent_id, body.intent)
    return {
        "raise_id": item.raise_id,
        "agent_id": item.agent_id,
        "intent": item.intent,
        "status": item.status.value,
    }


@router.get("/{room_id}/raise-hand")
def list_raise_hand(room_id: str, queue=Depends(get_raise_hand_queue)):
    return [
        {"raise_id": i.raise_id, "agent_id": i.agent_id, "intent": i.intent}
        for i in queue.list(room_id)
    ]


@router.post("/{room_id}/raise-hand/{raise_id}/release", status_code=201)
def release_hand(
    room_id: str,
    raise_id: str,
    queue=Depends(get_raise_hand_queue),
    speech=Depends(get_speech_service),
):
    items = {i.raise_id: i for i in queue.list(room_id)}
    if raise_id not in items:
        raise HTTPException(status_code=404, detail="raise hand not found")
    agent_id = items[raise_id].agent_id
    queue.release(room_id, raise_id)
    try:
        msg = speech.generate(room_id, agent_id)
    except SpeechError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return msg.model_dump()


@router.post("/{room_id}/raise-hand/{raise_id}/dismiss")
def dismiss_hand(room_id: str, raise_id: str, queue=Depends(get_raise_hand_queue)):
    queue.dismiss(room_id, raise_id)
    return {"ok": True}


# ---------- 结论与导出 ----------
@router.post("/{room_id}/conclusion", status_code=201)
def conclusion(room_id: str, svc=Depends(get_conclusion_service)):
    try:
        doc = svc.generate(room_id)
    except EmptyMemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return doc.model_dump()


@router.post("/{room_id}/export", status_code=201)
def export_transcript(room_id: str, svc=Depends(get_conclusion_service)):
    exp = svc.export_transcript(room_id)
    return exp.model_dump()
