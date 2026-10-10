from sqlmodel import Session, select

from app.domain.enums import OrchestrationMode, RoomStatus
from app.domain.models import HumanMember, Room, RoomAgent
from app.util import new_id


class RoomService:
    def __init__(self, engine, config_service):
        self.engine = engine
        self.config = config_service

    def create_room(self, proposition, orchestration_mode, agent_ids, host_name) -> Room:
        if not self.config.init_status()["ready"]:
            raise ValueError("system not initialized")
        if not agent_ids:
            raise ValueError("roster required")
        for agent_id in agent_ids:
            if self.config.get_agent(agent_id) is None:
                raise ValueError(f"agent not found: {agent_id}")
        room_id = new_id("room")
        host_id = new_id("mem")
        room = Room(
            room_id=room_id,
            proposition=proposition,
            orchestration_mode=OrchestrationMode(orchestration_mode),
            status=RoomStatus.PENDING,
            host_member_id=host_id,
        )
        with Session(self.engine) as s:
            s.add(room)
            s.add(
                HumanMember(
                    member_id=host_id, room_id=room_id, display_name=host_name, is_host=True
                )
            )
            for agent_id in agent_ids:
                s.add(RoomAgent(room_id=room_id, agent_id=agent_id))
            s.commit()
            s.refresh(room)
        return room

    def get_room(self, room_id: str) -> Room | None:
        with Session(self.engine) as s:
            return s.get(Room, room_id)

    def list_rooms(self) -> list[Room]:
        with Session(self.engine) as s:
            return list(s.exec(select(Room)).all())

    def selected_agent_ids(self, room_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(select(RoomAgent).where(RoomAgent.room_id == room_id)).all()
            return [r.agent_id for r in rows]

    def members(self, room_id: str) -> list[HumanMember]:
        with Session(self.engine) as s:
            return list(s.exec(select(HumanMember).where(HumanMember.room_id == room_id)).all())

    def add_member(self, room_id: str, display_name: str) -> HumanMember:
        if self.get_room(room_id) is None:
            raise ValueError("room not found")
        if any(m.display_name == display_name for m in self.members(room_id)):
            raise ValueError("duplicate display name")
        member = HumanMember(
            member_id=new_id("mem"), room_id=room_id, display_name=display_name, is_host=False
        )
        with Session(self.engine) as s:
            s.add(member)
            s.commit()
            s.refresh(member)
        return member

    def start_room(self, room_id: str) -> Room | None:
        return self._set_status(room_id, RoomStatus.IN_PROGRESS)

    def end_room(self, room_id: str) -> Room | None:
        room = self._set_status(room_id, RoomStatus.ENDED)
        if room is not None:
            from datetime import datetime, timezone

            with Session(self.engine) as s:
                row = s.get(Room, room_id)
                row.ended_at = datetime.now(timezone.utc)
                s.add(row)
                s.commit()
                s.refresh(row)
                room = row
        return room

    def _set_status(self, room_id: str, status: RoomStatus) -> Room | None:
        with Session(self.engine) as s:
            room = s.get(Room, room_id)
            if room is None:
                return None
            room.status = status
            s.add(room)
            s.commit()
            s.refresh(room)
            return room
