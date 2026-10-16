from app.domain.enums import SpeechPace
from scripts.agent_presets import (
    AGENT_PRESETS,
    is_placeholder_expertise,
    is_placeholder_persona,
)
from scripts.seed_config import ROSTER


def test_presets_cover_every_roster_role():
    assert set(AGENT_PRESETS) == {name for name, _, _ in ROSTER}


def test_preset_fields_are_substantive_not_placeholders():
    for name, (persona, expertise, style, pace) in AGENT_PRESETS.items():
        assert persona.strip() and expertise.strip() and style.strip()
        assert not is_placeholder_persona(name, persona)
        assert not is_placeholder_expertise(name, expertise)
        assert persona != expertise
        assert expertise != style
        assert persona != style
        assert isinstance(pace, SpeechPace)


def test_preset_styles_stay_short():
    """风格指令本身也须短：它是提示词的一部分，长了会挤占正文预算。"""
    for name, (_p, _e, style, _pace) in AGENT_PRESETS.items():
        assert len(style) <= 40, f"{name} 的发言风格过长：{len(style)} 字"


def test_placeholder_predicates_match_seed_shape_only():
    assert is_placeholder_persona("架构师", "架构师的角色人设")
    assert is_placeholder_expertise("架构师", "架构师的专长")
    assert not is_placeholder_persona("架构师", "关注系统边界与长期演化")
    assert not is_placeholder_expertise("架构师", "")
    assert not is_placeholder_persona("架构师", "业务领域专家的角色人设")
