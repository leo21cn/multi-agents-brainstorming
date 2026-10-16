"""结论生成门闩：一个房间同时只允许一次阶段结论生成，生成期间暂停发言。

内存态，与举手队列、发言权同性质——进程重启即清空，故本项无迁移。独立成模块而不
塞进结论端点或实时通道的私有字典，因为「生成中」这个事实要同时被 REST 结论端点、
REST 发言端点与实时通道三处读同一份：端点占用与释放、其余写路径据此拒绝新发言。
"""


class ConclusionGate:
    """每房间的结论生成占用集合：begin 成功即占用，end 释放。"""

    def __init__(self):
        self._rooms: set[str] = set()

    def begin(self, room_id: str) -> bool:
        """占用一次生成机会；已在生成中返回 False。调用与检查之间无 await，故原子。"""
        if room_id in self._rooms:
            return False
        self._rooms.add(room_id)
        return True

    def end(self, room_id: str) -> None:
        self._rooms.discard(room_id)

    def active(self, room_id: str) -> bool:
        return room_id in self._rooms


# 两条通道共用的拒绝文案：生成期间暂停发言，REST 与实时通道回同一句话
CONCLUDING_DETAIL = "阶段结论产出生成中，请稍等"
