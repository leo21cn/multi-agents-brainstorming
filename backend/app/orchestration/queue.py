from datetime import datetime, timezone

from app.domain.enums import RaiseHandStatus


class RaiseHand:
    def __init__(self, raise_id: str, room_id: str, agent_id: str, intent: str | None = None):
        self.raise_id = raise_id
        self.room_id = room_id
        self.agent_id = agent_id
        self.intent = intent
        self.status = RaiseHandStatus.QUEUED
        self.arrived_at = datetime.now(timezone.utc)


class RaiseHandQueue:
    """待发言队列：按到达顺序排列，放行/驳回后移出活动队列。"""

    def __init__(self):
        self._by_room: dict[str, list[RaiseHand]] = {}
        self._counter = 0

    def enqueue(self, room_id: str, agent_id: str, intent: str | None = None) -> RaiseHand:
        self._counter += 1
        item = RaiseHand(f"rh_{self._counter}", room_id, agent_id, intent)
        self._by_room.setdefault(room_id, []).append(item)
        return item

    def list(self, room_id: str) -> list[RaiseHand]:
        return list(self._by_room.get(room_id, []))

    def release(self, room_id: str, raise_id: str) -> None:
        self._remove(room_id, raise_id, RaiseHandStatus.RELEASED)

    def dismiss(self, room_id: str, raise_id: str) -> None:
        self._remove(room_id, raise_id, RaiseHandStatus.DISMISSED)

    def _remove(self, room_id: str, raise_id: str, status: RaiseHandStatus) -> None:
        items = self._by_room.get(room_id, [])
        for it in items:
            if it.raise_id == raise_id:
                it.status = status
        self._by_room[room_id] = [it for it in items if it.status == RaiseHandStatus.QUEUED]
