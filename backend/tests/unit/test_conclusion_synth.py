import json

import pytest

from app.models.demo_provider import DemoProvider
from app.models.provider import CONCLUSION_ASK, CONCLUSION_MARK
from app.services.conclusion_service import (
    EXTRA_SECTIONS_MAX,
    FIXED_SECTION_KEYS,
    SECTION_MAX_CHARS,
    SECTION_TARGET_CHARS,
    SYNTH_FIXED_KEY,
    build_conclusion_prompt,
    normalize_section_title,
    parse_conclusion_reply,
)

PAYLOAD = "\n".join(
    [
        f"{CONCLUSION_ASK}命题：是否推迟发布",
        "讨论记录：",
        "[架构师] 核心模块签名必须先确认。",
        "[交付负责人] 合规截止日不可移动。",
        "已抽取关键观点：",
        "- 架构师：签名判定归核心开发",
        "- 交付负责人：窗口缩减须书面确认",
        "已抽取分歧点：",
        "- 架构师：先重构再交付",
    ]
)


def _reply(**extra):
    return json.dumps({SYNTH_FIXED_KEY: "收敛于按期发布。", **extra}, ensure_ascii=False)


def test_prompt_carries_marker_and_records():
    msgs = build_conclusion_prompt(
        "是否推迟发布",
        ["[架构师] 核心模块签名必须先确认。"],
        ["- 架构师：签名判定归核心开发"],
        ["- 架构师：先重构再交付"],
    )
    assert msgs[0]["content"].startswith(CONCLUSION_MARK)
    assert msgs[1]["content"].startswith(CONCLUSION_ASK)
    body = msgs[1]["content"]
    assert "[架构师] 核心模块签名必须先确认。" in body
    assert "- 架构师：签名判定归核心开发" in body
    assert "禁止" in msgs[0]["content"] and "JSON 对象" in msgs[0]["content"]


def test_prompt_states_extra_section_rules():
    system = build_conclusion_prompt("P", [], [], [])[0]["content"]
    # 章数上限、字数目标与「同权重候选」都要写在提示词里，否则模型无从遵守
    assert str(EXTRA_SECTIONS_MAX) in system
    assert str(SECTION_TARGET_CHARS) in system
    assert "行动项与负责人" in system and "待定项" in system and "风险" in system
    assert "同权重" in system
    assert SYNTH_FIXED_KEY in system
    # 正文里的半角双引号会破坏 JSON，故提示词须明令改用「」
    assert "半角双引号" in system
    for key in FIXED_SECTION_KEYS:
        assert key in system


def test_parse_accepts_fenced_and_verbose_reply():
    reply = (
        "好的，如下：\n```json\n"
        + _reply(**{"上线前的验证": "甲", "尚未收敛的差异": "乙"})
        + "\n```\n以上。"
    )
    out = parse_conclusion_reply(reply)
    assert list(out) == [SYNTH_FIXED_KEY, "上线前的验证", "尚未收敛的差异"]
    assert out["上线前的验证"] == "甲"


def test_parse_keeps_reply_order():
    out = parse_conclusion_reply(_reply(**{"章一": "甲", "章二": "乙", "章三": "丙"}))
    assert list(out) == [SYNTH_FIXED_KEY, "章一", "章二", "章三"]


def test_parse_accepts_template_names_as_titles():
    # 三个通用名与其他候选同权重：讨论里有对应内容就该出现，禁用名单会把文体偏好做成硬失败
    out = parse_conclusion_reply(_reply(**{"行动项与负责人": "甲", "待定项": "乙", "风险": "丙"}))
    assert list(out)[1:] == ["行动项与负责人", "待定项", "风险"]


def test_parse_normalizes_title_marks():
    out = parse_conclusion_reply(_reply(**{"  ## 风险 ": "甲", "**待定项**": "乙"}))
    assert list(out)[1:] == ["风险", "待定项"]
    assert normalize_section_title("\u3000#行动项与负责人#") == "行动项与负责人"


def test_parse_accepts_up_to_ten_extra_sections():
    extra = {f"章{i}": f"正文{i}" for i in range(EXTRA_SECTIONS_MAX)}
    out = parse_conclusion_reply(_reply(**extra))
    assert len(out) == EXTRA_SECTIONS_MAX + 1


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "没有 JSON 的一段话",
        "{不是 JSON",
        json.dumps([1, 2]),
        json.dumps({"甲": "乙"}, ensure_ascii=False),
        json.dumps({SYNTH_FIXED_KEY: "  "}, ensure_ascii=False),
        json.dumps({SYNTH_FIXED_KEY: 3}),
        _reply(**{"甲": "  "}),
        _reply(**{"甲": 3}),
        _reply(**{"": "甲"}),
        _reply(**{"#": "甲"}),
        _reply(**{"标" * 21: "甲"}),
        _reply(**{"命题": "甲"}),
        _reply(**{"关键观点": "甲"}),
        _reply(**{"分歧点": "甲"}),
        _reply(**{SYNTH_FIXED_KEY + " ": "甲"}),
        _reply(**{"甲": "乙", "甲 ": "丙"}),
        _reply(**{f"章{i}": "甲" for i in range(EXTRA_SECTIONS_MAX + 1)}),
    ],
)
def test_parse_rejects_nonconforming_replies(reply):
    with pytest.raises(ValueError):
        parse_conclusion_reply(reply)


def test_parse_repairs_unescaped_inner_quotes():
    # 真实模型会把中文引号写成半角双引号，令整份回复成为非法 JSON；
    # 对象是扁平的，故结构引号可由其后一个非空白字符判定，内层引号补转义后即可解析
    raw = '{"初步结论／建议":"仅靠一句"临时方案兜住"并不足以消除风险。","合规截止日的刚性约束":"违约代价不可逆。"}'
    out = parse_conclusion_reply(raw)
    assert list(out) == [SYNTH_FIXED_KEY, "合规截止日的刚性约束"]
    assert out[SYNTH_FIXED_KEY] == '仅靠一句"临时方案兜住"并不足以消除风险。'


def test_parse_keeps_already_escaped_reply_untouched():
    reply = _reply(**{"引号章": "他说：\"照办\"。"})
    out = parse_conclusion_reply(reply)
    assert out["引号章"] == '他说："照办"。'


def test_rejection_messages_name_the_offending_part():
    with pytest.raises(ValueError) as missing:
        parse_conclusion_reply(json.dumps({"甲": "乙"}, ensure_ascii=False))
    assert SYNTH_FIXED_KEY in str(missing.value)
    with pytest.raises(ValueError) as dup:
        parse_conclusion_reply(_reply(**{"甲": "乙", "甲 ": "丙"}))
    assert "重复" in str(dup.value)
    with pytest.raises(ValueError) as clash:
        parse_conclusion_reply(_reply(**{"命题": "乙"}))
    assert "重名" in str(clash.value)


def test_section_over_target_but_within_bound_kept_intact():
    # 2000 字是目标不是刀口：稍微超出上限的完整段落原样收下
    text = "甲" * (SECTION_TARGET_CHARS + 200)
    out = parse_conclusion_reply(_reply(**{"长章": text}))
    assert out["长章"] == text
    assert len(out["长章"]) > SECTION_TARGET_CHARS


def test_section_exactly_at_bound_not_cut():
    text = "乙" * SECTION_MAX_CHARS
    out = parse_conclusion_reply(_reply(**{"长章": text}))
    assert len(out["长章"]) == SECTION_MAX_CHARS


def test_section_over_bound_cut_at_sentence():
    text = "这是一句完整的话。" * 400
    assert len(text) > SECTION_MAX_CHARS
    out = parse_conclusion_reply(_reply(**{"长章": text}))
    kept = out["长章"]
    assert len(kept) <= SECTION_MAX_CHARS
    assert kept.endswith("。")
    assert len(kept) > SECTION_TARGET_CHARS


def test_demo_conclusion_is_deterministic_and_derived():
    msgs = [
        {"role": "system", "content": CONCLUSION_MARK + "x"},
        {"role": "user", "content": PAYLOAD},
    ]
    first = DemoProvider().chat("any", msgs)
    second = DemoProvider().chat("any", msgs)
    assert first == second
    data = json.loads(first)
    assert SYNTH_FIXED_KEY in data
    assert all(isinstance(v, str) and v.strip() for v in data.values())
    assert "是否推迟发布" in data[SYNTH_FIXED_KEY]
    for v in data.values():
        assert "见关键观点" not in v and "待指派" not in v
    keys = [k for k in data if k != SYNTH_FIXED_KEY]
    assert len(keys) == 2
    assert all(k not in FIXED_SECTION_KEYS and len(k) <= 20 for k in keys)


def test_demo_conclusion_varies_with_records():
    other = PAYLOAD.replace("是否推迟发布", "是否加人")
    a = DemoProvider().chat(
        "m", [{"role": "system", "content": CONCLUSION_MARK}, {"role": "user", "content": PAYLOAD}]
    )
    b = DemoProvider().chat(
        "m", [{"role": "system", "content": CONCLUSION_MARK}, {"role": "user", "content": other}]
    )
    assert a != b
    assert "是否加人" in json.loads(b)[SYNTH_FIXED_KEY]


def test_demo_conclusion_extra_titles_come_from_points():
    msgs = [{"role": "system", "content": CONCLUSION_MARK}, {"role": "user", "content": PAYLOAD}]
    data = json.loads(DemoProvider().chat("m", msgs))
    keys = [k for k in data if k != SYNTH_FIXED_KEY]
    # 标题取自要点原文（去掉「署名：」前缀），不落回常量模板名
    assert keys == ["签名判定归核心开发", "先重构再交付"]
    assert "先重构再交付" in data["先重构再交付"]


def test_demo_conclusion_extra_titles_are_deduplicated():
    # 关键观点取发言前 60 字、分歧点取立场记忆即同一条发言前 200 字，故同源于一条发言时
    # 两个要点的前 12 字相同；离线派生须换一条要点，否则解析判为重名
    same = "\n".join(
        [
            f"{CONCLUSION_ASK}命题：是否推迟发布",
            "讨论记录：",
            "[架构师] 核心模块签名必须先确认。",
            "[交付负责人] 合规窗口缩减须书面确认。",
            "已抽取关键观点：",
            "- 架构师：核心模块签名必须先确认",
            "- 交付负责人：合规窗口缩减须书面确认",
            "已抽取分歧点：",
            "- 架构师：核心模块签名必须先确认",
        ]
    )
    data = json.loads(
        DemoProvider().chat(
            "m", [{"role": "system", "content": CONCLUSION_MARK}, {"role": "user", "content": same}]
        )
    )
    keys = [k for k in data if k != SYNTH_FIXED_KEY]
    assert len(keys) == len(set(keys)) == 2
    assert all(k not in FIXED_SECTION_KEYS for k in keys)
    parse_conclusion_reply(json.dumps(data, ensure_ascii=False))
