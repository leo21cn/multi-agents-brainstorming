from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.models.thinking import ResolvedPace

THOUGHT = "thought"
CONTENT = "content"
NOTICE = "notice"
END = "end"

# 意愿判定的系统提示词以本标记开头：离线提供方据此把判定与发言分开回复
WILLINGNESS_MARK = "[意愿判定]"
# 判定提问的固定前缀：离线提供方据此还原最新发言原文，令演示意图读起来像人话
WILLINGNESS_ASK = "最新发言："
# 结论综合的系统提示词标记与提问前缀：离线提供方据此从记录派生恒定的四章 JSON
CONCLUSION_MARK = "[结论综合]"
CONCLUSION_ASK = "讨论记录与已抽取要点："


@dataclass(frozen=True)
class StreamPiece:
    """流式片段。kind 取 THOUGHT／CONTENT／NOTICE／END 之一。

    END 必为最后一片，携带本次是否被截断。思考片段仅供呈现，不落库、不进上下文，
    故 A-12「落库内容与增量拼接一致」只对 CONTENT 片段成立。
    """

    kind: str
    text: str = ""
    truncated: bool = False


@runtime_checkable
class ModelProvider(Protocol):
    def chat(
        self, model_name: str, messages: list[dict], pace: "ResolvedPace | None" = None, **kw
    ) -> str: ...

    def stream_chat(
        self, model_name: str, messages: list[dict], pace: "ResolvedPace | None" = None, **kw
    ) -> Iterator[StreamPiece]: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
