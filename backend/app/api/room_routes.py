from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import MemberCreate, RoomCreate
from app.deps import get_room_service
from app.services.room_service import RoomService

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
