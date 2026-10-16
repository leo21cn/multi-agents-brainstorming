import json

from app.models.demo_provider import DemoProvider
from app.models.provider import WILLINGNESS_MARK
from app.orchestration.willingness import (
    Willingness,
    WillingnessService,
    build_willingness_prompt,
    parse_willingness_reply,
)


class FakeAgent:
    def __init__(self, name="架构师", model_name="qwen-max", expertise="技术债"):
        self.name = name
        self.model_name = model_name
        self.expertise = expertise


class EchoProvider:
    def __init__(self, reply):
        self._reply = reply
        self.seen = None

    def chat(self, model_name, messages, pace=None, **kw):
        self.seen = (model_name, messages, kw)
        return self._reply


def test_prompt_carries_mark_name_expertise_and_last_message():
    msgs = build_willingness_prompt("架构师", "技术债", "谁有意见？")

    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"].startswith(WILLINGNESS_MARK)
    assert "架构师" in msgs[0]["content"] and "技术债" in msgs[0]["content"]
    assert msgs[1] == {"role": "user", "content": "最新发言：谁有意见？"}


def test_parse_accepts_plain_json():
    assert parse_willingness_reply('{"want": true, "intent": "补一句排期风险"}') == Willingness(
        True, "补一句排期风险"
    )
    assert parse_willingness_reply('{"want": false, "intent": null}') == Willingness(False, None)


def test_parse_tolerates_fences_and_surrounding_prose():
    """真实模型常把 JSON 包进代码围栏或加一句解释；能取出对象就不该判为无效。"""
    fenced = '```json\n{"want": true, "intent": "想说"}\n```'
    assert parse_willingness_reply(fenced) == Willingness(True, "想说")
    assert parse_willingness_reply('好的。{"want": true, "intent": "想说"} 以上。') == Willingness(
        True, "想说"
    )


def test_parse_is_fail_closed():
    """取不出 JSON 对象、或 want 不严格为真，一律按不举手处理。

    把废话猜成举手比不举手更糟：主持人会看到一条来源不明的举手，并误以为是智能体的
    真实意愿。
    """
    for bad in [
        "",
        None,
        "我想说两句",
        "{want: true}",
        '["want", true]',
        '{"want": "true"}',
        '{"intent": "想说"}',
        "{ broken",
    ]:
        assert parse_willingness_reply(bad) == Willingness(False, None)


def test_parse_keeps_want_true_with_unusable_intent():
    """意图缺失或不是字符串时仍然举手，只是没有意图文本：举手本身不该被连坐丢掉。"""
    assert parse_willingness_reply('{"want": true, "intent": "   "}') == Willingness(True, None)
    assert parse_willingness_reply('{"want": true, "intent": 42}') == Willingness(True, None)
    assert parse_willingness_reply('{"want": true}') == Willingness(True, None)


def test_parse_truncates_long_intent():
    reply = json.dumps({"want": True, "intent": "很长" * 200}, ensure_ascii=False)

    assert len(parse_willingness_reply(reply).intent) == 120


def test_service_passes_timeout_and_disables_thinking():
    """判定只要一行 JSON：关思考、限时，且不受发言档位约束。"""
    provider = EchoProvider('{"want": true, "intent": "说一句"}')
    svc = WillingnessService(lambda agent: provider, timeout=7.5)

    assert svc.check(FakeAgent(), "谁有意见？") == Willingness(True, "说一句")

    model_name, messages, kw = provider.seen
    assert model_name == "qwen-max"
    assert kw == {"timeout": 7.5, "extra_body": {"enable_thinking": False}}
    assert messages[0]["content"].startswith(WILLINGNESS_MARK)


def test_service_skips_empty_last_message():
    provider = EchoProvider('{"want": true, "intent": "说一句"}')
    svc = WillingnessService(lambda agent: provider)

    assert svc.check(FakeAgent(), "   ") == Willingness(False, None)
    assert provider.seen is None


def test_demo_provider_is_deterministic_and_varies_by_agent():
    """离线判定须可复现，且不同智能体不全一致，否则演示看起来像串通好的。

    断言的是「两种结果都出现」而不是某个名字必然举手：哈希分布一改，钉死具体智能体的
    断言就成了伪约束。
    """
    provider = DemoProvider()
    last = "延期两周，怎么办？"
    reply = provider.chat("m", build_willingness_prompt("架构师", "技术债", last))

    assert reply == provider.chat("m", build_willingness_prompt("架构师", "技术债", last))
    assert parse_willingness_reply(reply) in (
        Willingness(True, "就「延期两周，怎么办？」补充一点"),
        Willingness(False, None),
    )

    wants = {
        parse_willingness_reply(
            provider.chat("m", build_willingness_prompt(name, "专长", last))
        ).want
        for name in ("架构师", "项目经理", "设计师", "质量保证")
    }
    assert wants == {True, False}


def test_demo_provider_ignores_willingness_kwargs():
    """判定路径的关键字参数（限时、关思考）不该改变离线判定的结果。"""
    provider = DemoProvider()
    msgs = build_willingness_prompt("架构师", "技术债", "说一句")

    plain = provider.chat("m", msgs)
    with_kw = provider.chat("m", msgs, None, timeout=1, extra_body={"enable_thinking": False})

    assert with_kw == plain
    assert parse_willingness_reply(with_kw) in (
        Willingness(True, "就「说一句」补充一点"),
        Willingness(False, None),
    )


def test_the_speaker_is_signed_in_the_system_section():
    """「主持人提问」与「成员附议」对「还有没有新角度」含义不同，故署名要进判定。"""
    from app.orchestration.willingness import build_willingness_prompt

    msgs = build_willingness_prompt("架构师", "技术债", "我附议", speaker="成员 林工")

    assert "最新发言来自成员 林工。" in msgs[0]["content"]
    assert "最新发言来自" not in msgs[0]["content"].split("。")[0]


def test_the_ask_prefix_stays_bare_so_offline_judgment_does_not_drift():
    """离线提供方按提问段的固定前缀还原最新发言原文，署名混进去会改变它的判定输入。"""
    from app.models.provider import WILLINGNESS_ASK
    from app.orchestration.willingness import build_willingness_prompt

    msgs = build_willingness_prompt("架构师", "技术债", "我附议", speaker="主持人 陈")

    assert msgs[1]["content"] == f"{WILLINGNESS_ASK}我附议"


def test_the_prompt_is_unchanged_without_a_speaker():
    from app.orchestration.willingness import build_willingness_prompt

    with_speaker = build_willingness_prompt("架构师", "技术债", "我附议", speaker=None)
    bare = build_willingness_prompt("架构师", "技术债", "我附议")

    assert with_speaker == bare
