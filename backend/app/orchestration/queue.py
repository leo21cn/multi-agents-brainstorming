from datetime import datetime, timezone

from app.domain.enums import RaiseHandStatus

# 举手来源：智能体自主判定、主持人代填、人类成员自己举手。界面据此标注，主持人须能
# 分辨每一条是谁提出的。
SOURCE_AUTO = "auto"
SOURCE_SIM = "sim"
SOURCE_MEMBER = "member"


class RaiseHand:
    """一条待发言举手：主体是智能体或人类成员，二者恰有一个非空。"""

    def __init__(
        self,
        raise_id: str,
        room_id: str,
        agent_id: str | None = None,
        intent: str | None = None,
        source: str = SOURCE_SIM,
        member_id: str | None = None,
    ):
        if (agent_id is None) == (member_id is None):
            raise ValueError("a hand belongs to exactly one of an agent or a human member")
        self.raise_id = raise_id
        self.room_id = room_id
        self.agent_id = agent_id
        self.member_id = member_id
        self.intent = intent
        self.source = source
        self.status = RaiseHandStatus.QUEUED
        self.arrived_at = datetime.now(timezone.utc)


class RaiseHandQueue:
    """待发言队列：按到达顺序排列，放行、驳回或撤回后移出活动队列。"""

    def __init__(self):
        self._by_room: dict[str, list[RaiseHand]] = {}
        self._counter = 0

    def enqueue(
        self,
        room_id: str,
        agent_id: str | None = None,
        intent: str | None = None,
        source: str = SOURCE_SIM,
        member_id: str | None = None,
    ) -> RaiseHand:
        self._counter += 1
        item = RaiseHand(f"rh_{self._counter}", room_id, agent_id, intent, source, member_id)
        self._by_room.setdefault(room_id, []).append(item)
        return item

    def list(self, room_id: str) -> list[RaiseHand]:
        return list(self._by_room.get(room_id, []))

    def find(self, room_id: str, agent_id: str) -> RaiseHand | None:
        """该智能体当前持有的举手；同一智能体在活动队列中至多一条。"""
        return next(
            (it for it in self._by_room.get(room_id, []) if it.agent_id == agent_id), None
        )

    def find_member(self, room_id: str, member_id: str) -> RaiseHand | None:
        """该人类成员当前持有的举手；同一成员在活动队列中至多一条。"""
        return next(
            (it for it in self._by_room.get(room_id, []) if it.member_id == member_id), None
        )

    def get(self, room_id: str, raise_id: str) -> RaiseHand | None:
        return next(
            (it for it in self._by_room.get(room_id, []) if it.raise_id == raise_id), None
        )

    def refresh(self, room_id: str, raise_id: str, intent: str | None) -> None:
        """改写举手意图而保留其位置与标识：上下文变了，想说的话也随之变。"""
        for it in self._by_room.get(room_id, []):
            if it.raise_id == raise_id:
                it.intent = intent

    def withdraw(self, room_id: str, raise_id: str) -> None:
        """举手者自己不再想说：移出活动队列并记为已撤回。"""
        self._remove(room_id, raise_id, RaiseHandStatus.WITHDRAWN)

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
