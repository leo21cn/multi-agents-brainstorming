"""发言权：主持人放行举手或直接 @ 一个人类成员后，该成员获得一次发言机会。

内存态，与举手队列同性质——进程重启即清空，重启后房间回到「没有人持权」，主持人
重新放行即可，故本项无迁移。独立成模块而不是放进实时通道的私有字典，因为通道不可用
时发言要走 REST 降级，两条路径必须读同一份发言权。
"""


class FloorGrant:
    """每房间的发言权集合：主持人不受约束，故集合里只会出现普通成员。"""

    def __init__(self):
        self._by_room: dict[str, set[str]] = {}

    def grant(self, room_id: str, member_id: str) -> None:
        self._by_room.setdefault(room_id, set()).add(member_id)

    def holds(self, room_id: str, member_id: str) -> bool:
        return member_id in self._by_room.get(room_id, set())

    def consume(self, room_id: str, member_id: str) -> None:
        """收掉一次发言权：消息落库后调用，故一次授予只兑一条发言。"""
        self._by_room.get(room_id, set()).discard(member_id)

    def revoke(self, room_id: str, member_id: str) -> None:
        """撤回已授予而尚未兑现的发言权。"""
        self._by_room.get(room_id, set()).discard(member_id)

    def holders(self, room_id: str) -> list[str]:
        """排序后返回：集合要整体下发给界面，次序不该随授予先后漂移。"""
        return sorted(self._by_room.get(room_id, set()))

    def clear(self, room_id: str) -> None:
        self._by_room.pop(room_id, None)


def address_grants(floor: FloorGrant, queue, room_id: str, sender, people: list[str]) -> list[dict]:
    """主持人的 @ 授予受话成员一次发言权；成员的 @ 只作标注，不授予。

    发言触发权全部经主持人：成员的 @ 若能授予，成员之间就可以私相授受。已被直接问到的
    成员，其排队举手同时作废——主持人不必再处理一条过期条目。返回本次授予的清单，由两条
    通道各自下发同一形状的 `floor_update`。
    """
    if not sender.is_host:
        return []
    granted = []
    for mid in people:
        if mid == sender.member_id:
            continue
        hand = queue.find_member(room_id, mid)
        if hand is not None:
            queue.withdraw(room_id, hand.raise_id)
        floor.grant(room_id, mid)
        granted.append({"member_id": mid, "reason": "addressed"})
    return granted
