from dataclasses import dataclass

from app.orchestration.strategy import SpeakerSelectionStrategy


@dataclass
class TurnResult:
    next_speaker: str | None = None


class Ag2Adapter:
    """AG2 集成点：把调度策略的选人结果转为房间的下一位发言者。

    框架以适配器隔离，业务层不直接依赖 AG2；如启用 AG2，仅在此替换实现。
    """

    def run_turn(self, room_state: dict, strategy: SpeakerSelectionStrategy) -> TurnResult:
        return TurnResult(next_speaker=strategy.select_next(room_state))
