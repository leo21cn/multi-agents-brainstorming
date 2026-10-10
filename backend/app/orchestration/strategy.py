from typing import Protocol


class SpeakerSelectionStrategy(Protocol):
    def select_next(self, room_state: dict) -> str | None: ...


class ManualStrategy:
    """人类主持控制：仅当主持人点名或放行时返回该智能体。"""

    def select_next(self, room_state: dict) -> str | None:
        directive = room_state.get("directive")
        if not directive:
            return None
        return directive.get("agent_id")


class _NotImplementedStrategy:
    def select_next(self, room_state: dict) -> str | None:
        raise NotImplementedError("本版未实现该编排模式")


class RoundRobinStrategy(_NotImplementedStrategy):
    pass


class AiModeratorStrategy(_NotImplementedStrategy):
    pass


class FreeForAllStrategy(_NotImplementedStrategy):
    pass
