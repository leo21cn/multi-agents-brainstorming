import secrets

from sqlmodel import Session, select

from app.domain.enums import MemberStatus, OrchestrationMode, RoomStatus
from app.domain.models import HumanMember, Room, RoomAgent
from app.util import new_id, token_matches


class RoomNotFound(Exception):
    """房间不存在。"""


class JoinRefused(Exception):
    """进房被拒；消息即界面呈现的原因。"""


class RoomService:
    def __init__(self, engine, config_service):
        self.engine = engine
        self.config = config_service

    def create_room(self, proposition, orchestration_mode, agent_ids, host_name) -> tuple[Room, str]:
        """建房并把创建者登记为主持人；返回房间与主持人的成员令牌。

        令牌只在此处出现一次：任何读接口都不下发，丢了只能凭管理令牌认领。
        """
        if not self.config.init_status()["ready"]:
            raise ValueError("system not initialized")
        if not agent_ids:
            raise ValueError("roster required")
        for agent_id in agent_ids:
            if self.config.get_agent(agent_id) is None:
                raise ValueError(f"agent not found: {agent_id}")
        room_id = new_id("room")
        host_id = new_id("mem")
        host_token = secrets.token_hex(16)
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
                    member_id=host_id,
                    room_id=room_id,
                    display_name=host_name,
                    is_host=True,
                    token=host_token,
                )
            )
            for agent_id in agent_ids:
                s.add(RoomAgent(room_id=room_id, agent_id=agent_id))
            s.commit()
            s.refresh(room)
        return room, host_token

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

    def member_brief(self, room_id: str, member_id: str) -> tuple[str, bool] | None:
        """成员显示名与是否主持人；给发言署名用，成员不在本房间时返回 None。"""
        with Session(self.engine) as s:
            m = s.get(HumanMember, member_id)
            if m is None or m.room_id != room_id:
                return None
            return m.display_name, m.is_host

    def join_room(self, room_id: str, display_name: str) -> tuple[HumanMember, str]:
        """受邀者自助进房：登记为普通成员并发放令牌。

        昵称除在人类成员内唯一外还不得与阵容内智能体同名——同名会让 @ 解析出现二义，
        在进房时一次拒绝比事后定优先级规则干净。
        """
        room = self.get_room(room_id)
        if room is None:
            raise RoomNotFound(room_id)
        if room.status == RoomStatus.ENDED:
            raise JoinRefused("房间已结束，不再接受新成员加入")
        if any(m.display_name == display_name for m in self.members(room_id)):
            raise JoinRefused("昵称已被占用，请换一个")
        for aid in self.selected_agent_ids(room_id):
            agent = self.config.get_agent(aid)
            if agent is not None and agent.name == display_name:
                raise JoinRefused("昵称已被占用，请换一个")
        token = secrets.token_hex(16)
        member = HumanMember(
            member_id=new_id("mem"),
            room_id=room_id,
            display_name=display_name,
            is_host=False,
            token=token,
        )
        with Session(self.engine) as s:
            s.add(member)
            s.commit()
            s.refresh(member)
        return member, token

    def reissue_token(self, room_id: str, member_id: str) -> tuple[HumanMember, str]:
        """为既有成员重新发放令牌：主持人换了浏览器就靠这条找回身份。

        认领同时把成员置回在线——已退出成员经认领重新回到房间。
        """
        with Session(self.engine) as s:
            member = s.get(HumanMember, member_id)
            if member is None or member.room_id != room_id:
                raise ValueError("member not in room")
            token = secrets.token_hex(16)
            member.token = token
            member.status = MemberStatus.ONLINE
            s.add(member)
            s.commit()
            s.refresh(member)
        return member, token

    def authenticate(self, room_id: str, member_id: str, token: str | None) -> HumanMember | None:
        """校验成员令牌；令牌为空（既有房间未认领）或不匹配一律返回 None。"""
        with Session(self.engine) as s:
            member = s.get(HumanMember, member_id)
            if member is None or member.room_id != room_id:
                return None
            if not member.token or not token_matches(token, member.token):
                return None
            return member

    def start_room(self, room_id: str) -> Room | None:
        return self._set_status(room_id, RoomStatus.IN_PROGRESS)

    def set_host(self, room_id: str, member_id: str) -> Room | None:
        with Session(self.engine) as s:
            room = s.get(Room, room_id)
            if room is None:
                return None
            member = s.get(HumanMember, member_id)
            if member is None or member.room_id != room_id:
                raise ValueError("member not in room")
            if member.status == MemberStatus.LEFT:
                raise ValueError("该成员已退出聊天室，不能设为主持人")
            for m in s.exec(select(HumanMember).where(HumanMember.room_id == room_id)).all():
                m.is_host = m.member_id == member_id
                s.add(m)
            room.host_member_id = member_id
            s.add(room)
            s.commit()
            s.refresh(room)
            return room

    def leave_room(self, room_id: str, member_id: str) -> HumanMember:
        """成员退出聊天室：置已退出并清令牌。

        主持人且房间未结束时拒绝——先移交或先结束由服务端强制，界面门控不是唯一的一层。
        令牌清空后旧身份立即失效；认领（reissue_token）即恢复在线。
        """
        with Session(self.engine) as s:
            member = s.get(HumanMember, member_id)
            if member is None or member.room_id != room_id:
                raise ValueError("member not in room")
            room = s.get(Room, room_id)
            if room is None:
                raise RoomNotFound(room_id)
            if member.is_host and room.status != RoomStatus.ENDED:
                raise ValueError("你是主持人，退出前请先移交主持或结束房间")
            member.status = MemberStatus.LEFT
            member.token = None
            s.add(member)
            s.commit()
            s.refresh(member)
        return member

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
