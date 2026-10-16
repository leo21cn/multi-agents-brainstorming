"""连接级身份：把「谁在说话」与「谁有权做什么」认在请求与连接上。

界面门控只是第一层，真正的边界在这里。身份经两个请求头（REST）或连接查询串（实时
通道）携带；校验通过后绑定到本次请求或整条连接，后续动作不再逐条自证。
"""

from fastapi import Header, HTTPException

from app.services.room_service import RoomService

IDENTITY_INVALID = "身份缺失或已失效，请重新进房"
NOT_HOST = "该操作只有主持人可执行"
NO_FLOOR = "尚未获得发言权：举手并经主持人放行，或由主持人 @ 你"


def resolve_member(svc: RoomService, room_id: str, member_id, token):
    """校验成员令牌并返回成员；缺任一项或不匹配一律 None，由调用方决定如何拒绝。"""
    if not member_id:
        return None
    return svc.authenticate(room_id, member_id, token)


def member_credentials(
    x_member_id: str | None = Header(default=None),
    x_member_token: str | None = Header(default=None),
) -> tuple[str | None, str | None]:
    return x_member_id, x_member_token


def require_member(room_id: str, svc: RoomService, creds):
    member = resolve_member(svc, room_id, *creds)
    if member is None:
        raise HTTPException(status_code=401, detail=IDENTITY_INVALID)
    return member


def require_host(member) -> None:
    if not member.is_host:
        raise HTTPException(status_code=403, detail=NOT_HOST)


def require_host_member(room_id: str, svc: RoomService, creds):
    """主持人专属端点的入口：先认身份，再认角色。"""
    member = require_member(room_id, svc, creds)
    require_host(member)
    return member
