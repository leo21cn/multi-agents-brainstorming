from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.deps import get_raise_hand_queue, get_room_service, get_speech_service
from app.domain.enums import SourceType
from app.services.speech_service import SpeechError

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
            await ws.send_json(event)


manager = ConnectionManager()


@router.websocket("/rooms/{room_id}/ws")
async def room_ws(
    ws: WebSocket,
    room_id: str,
    speech=Depends(get_speech_service),
    room_service=Depends(get_room_service),
    queue=Depends(get_raise_hand_queue),
):
    await manager.connect(room_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")
            if kind == "point":
                await _speak(ws, room_id, data["agent_id"], data.get("intent"), speech)
            elif kind == "raise_hand":
                item = queue.enqueue(room_id, data["agent_id"], data.get("intent"))
                await manager.broadcast(
                    room_id,
                    {"type": "raise_hand", "agent_id": item.agent_id, "raise_id": item.raise_id},
                )
            elif kind == "release":
                items = {i.raise_id: i for i in queue.list(room_id)}
                if data.get("raise_id") in items:
                    agent_id = items[data["raise_id"]].agent_id
                    queue.release(room_id, data["raise_id"])
                    await _speak(ws, room_id, agent_id, None, speech)
                await manager.broadcast(room_id, {"type": "raise_hand_update"})
            elif kind == "dismiss":
                queue.dismiss(room_id, data.get("raise_id", ""))
                await manager.broadcast(room_id, {"type": "raise_hand_update"})
            elif kind == "human_message":
                msg = speech.memory.append_message(
                    room_id, SourceType.HUMAN, "host", data.get("content", "")
                )
                await manager.broadcast(room_id, {"type": "message", "message": msg.model_dump(mode="json")})
            elif kind == "room_state":
                room = room_service.get_room(room_id)
                await ws.send_json(
                    {"type": "room_state", "room": room.model_dump(mode="json") if room else None}
                )
    except WebSocketDisconnect:
        manager.disconnect(room_id, ws)


async def _speak(ws: WebSocket, room_id, agent_id, intent, speech) -> None:
    try:
        msg = speech.generate(room_id, agent_id, intent)
        await manager.broadcast(room_id, {"type": "message", "message": msg.model_dump(mode="json")})
    except SpeechError as exc:
        await manager.broadcast(room_id, {"type": "speech_failed", "detail": str(exc)})
