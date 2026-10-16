from app.domain.enums import SpeechPace
from app.models.thinking import (
    DEFAULT_PACE,
    PACE_PARAMS,
    resolve,
    supports_thinking_budget,
)


def test_default_pace_is_balanced():
    assert DEFAULT_PACE is SpeechPace.BALANCED
    assert resolve(None, "qwen-max").pace is SpeechPace.BALANCED


def test_flash_turns_thinking_off():
    r = resolve(SpeechPace.FLASH, "qwen3.8-max")
    assert r.thinking_enabled is False
    assert r.thinking_budget is None
    assert r.degraded is False


def test_balanced_and_deep_carry_budget_on_supported_model():
    assert resolve(SpeechPace.BALANCED, "qwen-max").thinking_budget == 256
    assert resolve(SpeechPace.DEEP, "qwen-max").thinking_budget == 1024
    assert resolve(SpeechPace.DEEP, "qwen-max").degraded is False


def test_max_chars_grows_with_pace():
    assert [PACE_PARAMS[p][1] for p in SpeechPace] == [240, 600, 1800]
    assert resolve(SpeechPace.FLASH, "x").max_chars == 240


def test_ceiling_is_a_loose_bound_above_the_prompt_target():
    """安全界只在模型无视提示词目标时兜底，故必然宽于目标本身。"""
    assert [resolve(p, "x").ceiling_chars for p in SpeechPace] == [360, 900, 2700]


def test_unsupported_model_degrades_openly_not_silently():
    """不认思考预算的模型退化为开思考不限预算，且必须置降级标记。"""
    r = resolve(SpeechPace.DEEP, "deepseek-v4.1-flash")
    assert r.budget_supported is False
    assert r.thinking_enabled is True
    assert r.thinking_budget is None
    assert r.degraded is True


def test_prefix_rule_is_case_insensitive_and_null_safe():
    assert supports_thinking_budget("Qwen3.8-Max")
    assert supports_thinking_budget("qwen-plus")
    assert not supports_thinking_budget("deepseek-v4.1-flash")
    assert not supports_thinking_budget("gpt-4o")
    assert not supports_thinking_budget("")
    assert not supports_thinking_budget(None)
