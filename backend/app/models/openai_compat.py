from typing import TYPE_CHECKING, Iterator

from openai import OpenAI

from app.models.cutoff import UnitGate
from app.models.provider import CONTENT, END, NOTICE, THOUGHT, ModelProvider, StreamPiece

if TYPE_CHECKING:
    from app.models.thinking import ResolvedPace

DEFAULT_EMBEDDING_MODEL = "text-embedding-v3"

THINKING_REJECTED_NOTICE = "该模型不接受思考参数，已按关思考生成"

# 端点对未知 extra_body 键静默忽略，故 400 只可能来自取值非法；模型名错误是 404，不会被误伤。
_THINKING_PARAM_MARKERS = ("enable_thinking", "thinking_budget")


def _is_thinking_param_rejection(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) != 400:
        return False
    text = str(exc).lower()
    return any(marker in text for marker in _THINKING_PARAM_MARKERS)


class OpenAICompatProvider:
    """OpenAI 兼容提供方实现，可用于 DashScope 兼容端点。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        client_factory=OpenAI,
    ):
        self._client = client_factory(base_url=base_url, api_key=api_key)
        self._embedding_model = embedding_model or DEFAULT_EMBEDDING_MODEL

    @staticmethod
    def _extra_body(pace: "ResolvedPace | None") -> dict:
        if pace is None:
            return {}
        if not pace.thinking_enabled:
            return {"enable_thinking": False}
        if pace.thinking_budget is not None:
            return {"enable_thinking": True, "thinking_budget": pace.thinking_budget}
        return {"enable_thinking": True}

    def _args(
        self,
        model_name: str,
        messages: list[dict],
        pace: "ResolvedPace | None",
        stream: bool,
        kw: dict,
    ) -> dict:
        args = {"model": model_name, "messages": messages, **kw}
        if stream:
            args["stream"] = True
        body = self._extra_body(pace)
        if body:
            args["extra_body"] = body
        return args

    def chat(self, model_name: str, messages: list[dict], pace: "ResolvedPace | None" = None, **kw) -> str:
        """一次性路径复用流式实现：断流与截断判定只有一处，两条路径的正文必然一致。"""
        return "".join(
            p.text for p in self.stream_chat(model_name, messages, pace, **kw) if p.kind == CONTENT
        )

    def stream_chat(
        self, model_name: str, messages: list[dict], pace: "ResolvedPace | None" = None, **kw
    ) -> Iterator[StreamPiece]:
        """流式生成。端点拒绝思考参数时去掉参数重试一次，并下发可见的降级提示。

        重试只去掉思考参数，pace 本体保留——安全界与思考无关，丢了会让重试后的回复不受长度约束。
        已下发过任何片段则不重试，否则客户端会看到重复内容。
        """
        with_thinking = bool(self._extra_body(pace))
        retried = False
        while True:
            emitted_any = False
            try:
                for piece in self._iter_pieces(model_name, messages, pace, kw, with_thinking):
                    emitted_any = True
                    yield piece
                return
            except Exception as exc:
                if (
                    emitted_any
                    or retried
                    or not with_thinking
                    or not _is_thinking_param_rejection(exc)
                ):
                    raise
                retried = True
                with_thinking = False
                yield StreamPiece(NOTICE, THINKING_REJECTED_NOTICE)

    def _iter_pieces(
        self,
        model_name: str,
        messages: list[dict],
        pace: "ResolvedPace | None",
        kw: dict,
        with_thinking: bool = True,
    ) -> Iterator[StreamPiece]:
        """逐块转发思考与正文，正文经句界门控发射。

        门控只在整句到达后才下发增量，故兜底断流时无需回收已下发内容，界面呈现与落库
        天然一致；断流点落在句界，正文不会停在半句。
        """
        gate = UnitGate(pace.ceiling_chars) if pace else None
        stream = self._client.chat.completions.create(
            **self._args(model_name, messages, pace if with_thinking else None, True, kw)
        )
        truncated = False
        try:
            for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                thought = getattr(delta, "reasoning_content", None)
                if thought:
                    yield StreamPiece(THOUGHT, thought)
                piece = getattr(delta, "content", None)
                if piece:
                    if gate is None:
                        yield StreamPiece(CONTENT, piece)
                    else:
                        for unit in gate.feed(piece):
                            yield StreamPiece(CONTENT, unit)
                finish = getattr(choice, "finish_reason", None)
                if finish == "length":
                    truncated = True
                if gate is not None and gate.stopped:
                    # 断流即放弃未读完的流；本块带 finish_reason 说明模型已自行收笔，无内容被丢
                    truncated = truncated or finish is None
                    break
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
        if gate is not None:
            for unit in gate.flush():
                yield StreamPiece(CONTENT, unit)
            truncated = truncated or gate.truncated
        yield StreamPiece(END, "", truncated)

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = self._client.embeddings.create(model=self._embedding_model, input=texts)
        return [d.embedding for d in r.data]
