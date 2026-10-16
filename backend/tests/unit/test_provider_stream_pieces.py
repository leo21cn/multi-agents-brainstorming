from types import SimpleNamespace

from app.domain.enums import SpeechPace
from app.models.demo_provider import DemoProvider
from app.models.openai_compat import THINKING_REJECTED_NOTICE, OpenAICompatProvider
from app.models.provider import CONTENT, END, NOTICE, THOUGHT
from app.models.thinking import resolve


def _chunk(reasoning=None, content=None, finish=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(reasoning_content=reasoning, content=content),
                finish_reason=finish,
            )
        ]
    )


def _http_error(status, text):
    return type(
        f"E{status}", (Exception,), {"status_code": status, "__str__": lambda self: text}
    )()


class FakeCompletions:
    def __init__(self, chunks, error=None):
        self._chunks = chunks
        self._error = error
        self.calls = []
        self.closed = 0

    def create(self, **kw):
        self.calls.append(kw)
        if self._error is not None:
            # 一次性：模拟端点在去掉非法参数后即接受请求
            err, self._error = self._error, None
            raise err
        outer = self

        class _Stream:
            def __iter__(_self):
                return iter(outer._chunks)

            def close(_self):
                outer.closed += 1

        return _Stream()


class FakeClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


def _provider(chunks, error=None):
    comp = FakeCompletions(chunks, error)
    return (
        OpenAICompatProvider("http://x", "sk-x", client_factory=lambda **kw: FakeClient(comp)),
        comp,
    )


def _kinds(pieces):
    return [p.kind for p in pieces]


def test_thought_and_content_are_separate_kinds():
    prov, _ = _provider([
        _chunk(reasoning="想一想"),
        _chunk(reasoning="再想"),
        _chunk(content="结论"),
        _chunk(finish="stop"),
    ])
    pieces = list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.BALANCED, "qwen-max")))
    assert _kinds(pieces) == [THOUGHT, THOUGHT, CONTENT, END]
    assert "".join(p.text for p in pieces if p.kind == THOUGHT) == "想一想再想"
    assert "".join(p.text for p in pieces if p.kind == CONTENT) == "结论"
    assert pieces[-1].truncated is False


def test_content_stops_at_the_ceiling_on_a_sentence_boundary():
    prov, comp = _provider([
        _chunk(content="甲。" * 300),
        _chunk(content="乙。" * 300),
        _chunk(finish="stop"),
    ])
    pace = resolve(SpeechPace.FLASH, "qwen-max")
    pieces = list(prov.stream_chat("qwen-max", [], pace))
    text = "".join(p.text for p in pieces if p.kind == CONTENT)
    assert len(text) == pace.ceiling_chars == 360
    assert text.endswith("。"), "兜底断流必须落在句界，不能停在半句"
    assert pieces[-1].kind == END and pieces[-1].truncated is True
    assert comp.closed == 1, "提前 break 后必须关闭流，否则连接悬挂"


def test_content_within_the_ceiling_is_never_cut():
    """模型自行收束在目标内时客户端零干预，也不打截断标记。"""
    prov, _ = _provider([_chunk(content="甲。" * 100, finish="stop")])
    pieces = list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.FLASH, "qwen-max")))
    text = "".join(p.text for p in pieces if p.kind == CONTENT)
    assert text == "甲。" * 100
    assert pieces[-1].truncated is False


def test_boundaryless_content_is_held_then_emitted_whole():
    """无句界的增量先蓄在门控内，流结束时整段补发，不做字符级切割。"""
    prov, _ = _provider([_chunk(content="甲" * 100), _chunk(content="乙" * 100)])
    pieces = list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.FLASH, "qwen-max")))
    assert [p.text for p in pieces if p.kind == CONTENT] == ["甲" * 100 + "乙" * 100]
    assert pieces[-1].truncated is False


def test_finish_reason_length_flags_truncated():
    prov, _ = _provider([_chunk(content="短"), _chunk(finish="length")])
    pieces = list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.DEEP, "qwen-max")))
    assert pieces[-1].truncated is True


def test_flash_sends_enable_thinking_false():
    prov, comp = _provider([_chunk(content="甲", finish="stop")])
    list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.FLASH, "qwen-max")))
    assert comp.calls[0]["extra_body"] == {"enable_thinking": False}


def test_balanced_sends_thinking_budget():
    prov, comp = _provider([_chunk(content="甲", finish="stop")])
    list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.BALANCED, "qwen-max")))
    assert comp.calls[0]["extra_body"] == {"enable_thinking": True, "thinking_budget": 256}


def test_degraded_pace_sends_enable_thinking_only():
    prov, comp = _provider([_chunk(content="甲", finish="stop")])
    pace = resolve(SpeechPace.DEEP, "deepseek-v4.1-flash")
    assert pace.degraded is True
    list(prov.stream_chat("deepseek-v4.1-flash", [], pace))
    assert comp.calls[0]["extra_body"] == {"enable_thinking": True}


def test_no_pace_sends_no_extra_body():
    prov, comp = _provider([_chunk(content="甲", finish="stop")])
    list(prov.stream_chat("qwen-max", [], None))
    assert "extra_body" not in comp.calls[0]


def test_bad_request_on_thinking_param_retries_without_and_notices():
    prov, comp = _provider(
        [_chunk(content="甲", finish="stop")],
        error=_http_error(400, "Error code: 400 - InvalidParameter: parameters.thinking_budget"),
    )
    pieces = list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.DEEP, "qwen-max")))
    assert _kinds(pieces) == [NOTICE, CONTENT, END]
    assert [p.text for p in pieces if p.kind == NOTICE] == [THINKING_REJECTED_NOTICE]
    assert len(comp.calls) == 2
    assert "extra_body" not in comp.calls[1]


def test_retry_keeps_the_length_ceiling():
    """重试只去掉思考参数，安全界必须保留，否则重试后的回复不受长度约束。"""
    prov, _ = _provider(
        [_chunk(content="甲。" * 500, finish="stop")],
        error=_http_error(400, "parameters.enable_thinking is invalid"),
    )
    pace = resolve(SpeechPace.FLASH, "qwen-max")
    pieces = list(prov.stream_chat("qwen-max", [], pace))
    text = "".join(p.text for p in pieces if p.kind == CONTENT)
    assert len(text) == pace.ceiling_chars
    assert text.endswith("。")


def test_model_not_found_is_not_retried():
    prov, comp = _provider([], error=_http_error(404, "The model does not exist"))
    try:
        list(prov.stream_chat("qwen-nope", [], resolve(SpeechPace.DEEP, "qwen-nope")))
        raise AssertionError("应当抛出")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    assert len(comp.calls) == 1


def test_error_after_content_started_is_not_retried():
    class HalfStream:
        def __iter__(self):
            # 带句界：门控下发过正文后，重试会让客户端看到重复内容
            yield _chunk(content="开头。")
            raise _http_error(400, "parameters.thinking_budget")

        def close(self):
            pass

    comp = FakeCompletions([])
    comp.create = lambda **kw: (comp.calls.append(kw), HalfStream())[1]
    prov = OpenAICompatProvider("http://x", "sk-x", client_factory=lambda **kw: FakeClient(comp))
    try:
        list(prov.stream_chat("qwen-max", [], resolve(SpeechPace.DEEP, "qwen-max")))
        raise AssertionError("应当抛出")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    assert len(comp.calls) == 1, "已下发正文后重试会让客户端看到重复内容"


def test_chat_joins_the_stream_and_respects_the_ceiling():
    """一次性路径复用流式实现：正文与截断口径与 stream_chat 完全一致。"""
    chunks = [_chunk(content="甲。" * 500, finish="stop")]
    pace = resolve(SpeechPace.FLASH, "qwen-max")
    streamed = "".join(
        p.text
        for p in _provider(chunks)[0].stream_chat("qwen-max", [], pace)
        if p.kind == CONTENT
    )
    once = _provider(chunks)[0].chat("qwen-max", [], pace)
    assert once == streamed
    assert len(once) == pace.ceiling_chars
    assert once.endswith("。")


def test_demo_provider_emits_pieces_and_respects_ceiling():
    demo = DemoProvider()
    msgs = [{"role": "system", "content": "你是架构师。"}, {"role": "user", "content": "延期两周怎么办"}]
    pace = resolve(SpeechPace.FLASH, "demo")
    pieces = list(demo.stream_chat("demo", msgs, pace))
    assert pieces[-1].kind == END
    text = "".join(p.text for p in pieces if p.kind == CONTENT)
    assert len(text) <= pace.ceiling_chars
    assert text == demo.chat("demo", msgs, pace)


def test_demo_provider_still_extracts_agent_name_from_first_sentence():
    """提示词首句须保持「你是{name}。」：离线提供方以句号切分取名。"""
    demo = DemoProvider()
    out = demo.chat(
        "demo",
        [{"role": "system", "content": "你是批判者。\n本次头脑风暴的参与者之一。"}],
        None,
    )
    assert out.startswith("你是批判者 的判断：")
