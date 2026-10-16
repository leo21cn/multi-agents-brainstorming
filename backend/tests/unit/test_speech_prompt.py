from app.services.speech_service import build_system_prompt


def test_prompt_includes_speaking_style():
    p = build_system_prompt("架构师", "资深架构师", "技术债", "先定性再给方案", 600)
    assert "你是架构师。" in p
    assert "专长：技术债" in p
    assert "发言风格：先定性再给方案" in p


def test_prompt_omits_style_when_empty():
    assert "发言风格" not in build_system_prompt("架构师", "p", "e", None, 600)
    assert "发言风格" not in build_system_prompt("架构师", "p", "e", "", 600)


def test_prompt_asks_for_a_complete_sentence_within_the_target():
    """上限只是目标，客户端不再逐字硬切，故完整性必须由提示词要求。"""
    p = build_system_prompt("架构师", "p", "e", None, 600)
    assert "正文 600 字以内" in p
    assert "以完整句收束" in p
    assert "说不完的观点宁可不提" in p


def test_prompt_no_longer_licenses_half_finished_speech():
    assert "允许不完整" not in build_system_prompt("架构师", "p", "e", None, 240)
