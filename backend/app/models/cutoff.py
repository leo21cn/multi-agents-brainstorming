"""正文长度的安全界与句界门控。

档位字数目标是提示词下达的范围，由模型自行收束；安全界只是兜底，防止模型无视要求时无限输出。
兜底触发时按句界收尾，保证落库与界面呈现的正文以完整句结束——半句被切既像系统故障，
也让导出成为失真的记录。
"""

from dataclasses import dataclass, field

UNIT_TERMINATORS = ("。", "！", "？", "；", "…", "\n")
CLAUSE_TERMINATORS = ("，", "、", "：", ",")


def _find(text: str, terminators: tuple[str, ...]) -> int:
    hits = [i for i in (text.find(t) for t in terminators) if i >= 0]
    return min(hits) if hits else -1


def _rfind(text: str, terminators: tuple[str, ...]) -> int:
    hits = [i for i in (text.rfind(t) for t in terminators) if i >= 0]
    return max(hits) if hits else -1


def cut_at_boundary(text: str, limit: int) -> tuple[str, bool]:
    """截到不超过 limit 且以界收尾的前缀；返回（保留部分，是否有丢弃）。"""
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    for terminators in (UNIT_TERMINATORS, CLAUSE_TERMINATORS):
        at = _rfind(window, terminators)
        if at >= 0:
            return window[: at + 1], True
    return window, True


@dataclass
class UnitGate:
    """流式句界门控：只发射完整句，安全界内放不下下一句时就地收束。

    `stopped` 表示不再发射，供调用方提前断流；`truncated` 表示确有内容被丢弃。两者必须
    分开：正文恰好填满安全界时前者为真而后者为假，此时不该打截断标注。
    """

    limit: int
    _pending: str = field(default="", init=False)
    _emitted: int = field(default=0, init=False)
    _dropped: bool = field(default=False, init=False)
    stopped: bool = field(default=False, init=False)

    def feed(self, delta: str) -> list[str]:
        if self.stopped:
            self._stop(bool(delta))
            return []
        self._pending += delta
        out: list[str] = []
        while True:
            budget = self.limit - self._emitted
            if budget <= 0:
                self._stop(bool(self._pending))
                break
            at = _find(self._pending, UNIT_TERMINATORS)
            if at < 0 and len(self._pending) < budget:
                break  # 尚无句界且仍有预算：等更多增量
            if at >= 0 and at + 1 <= budget:
                out.append(self._take(self._pending[: at + 1]))
                continue
            # 下一句放不下，或长行无句界：就地收束，其后内容不再发射
            tail = self._last_resort(budget)
            if tail:
                out.append(self._take(tail))
            self._stop(True)
            break
        return out

    def flush(self) -> list[str]:
        """流自然结束时补发尾部：模型已收笔，尾部无句界也不算截断。"""
        if self.stopped or not self._pending:
            return []
        if self._emitted + len(self._pending) <= self.limit:
            return [self._take(self._pending)]
        self._stop(True)
        return []

    @property
    def truncated(self) -> bool:
        return self._dropped

    def _last_resort(self, budget: int) -> str:
        """收束处的退让：仅在尚未发出任何内容时使用，否则会产出半句。

        宁可短也不出空正文——首句就超预算时退到子句界，连子句界都没有才硬切。
        """
        if self._emitted > 0:
            return ""
        window = self._pending[:budget]
        at = _rfind(window, CLAUSE_TERMINATORS)
        return window[: at + 1] if at >= 0 else window

    def _take(self, text: str) -> str:
        self._pending = self._pending[len(text) :]
        self._emitted += len(text)
        return text

    def _stop(self, dropped: bool) -> None:
        self.stopped = True
        self._dropped = self._dropped or dropped
