from app.models.cutoff import UnitGate, cut_at_boundary


def test_cut_within_limit_keeps_all():
    assert cut_at_boundary("甲。乙。", 10) == ("甲。乙。", False)


def test_cut_falls_back_to_last_unit_boundary():
    kept, dropped = cut_at_boundary("甲甲甲。乙乙乙。丙丙丙。", 8)
    assert kept == "甲甲甲。乙乙乙。"
    assert dropped is True


def test_cut_falls_back_to_clause_then_hard():
    kept, _ = cut_at_boundary("甲甲甲，乙乙乙，丙丙丙，", 5)
    assert kept == "甲甲甲，"
    kept, dropped = cut_at_boundary("甲乙丙丁戊己庚", 4)
    assert kept == "甲乙丙丁" and dropped is True


def test_gate_emits_whole_units_only():
    g = UnitGate(6)
    out = g.feed("甲甲。") + g.feed("乙乙。") + g.feed("丙丙丙丙丙。")
    assert out == ["甲甲。", "乙乙。"]
    assert g.flush() == []
    assert g.truncated is True


def test_gate_flushes_tail_that_fits():
    g = UnitGate(10)
    out = g.feed("甲甲。乙乙")
    assert out == ["甲甲。"]
    assert g.flush() == ["乙乙"]
    assert g.truncated is False


def test_gate_hard_cuts_unit_longer_than_budget():
    g = UnitGate(4)
    out = g.feed("甲乙丙丁戊己")
    assert out == ["甲乙丙丁"]
    assert g.truncated is True


def test_gate_filling_the_ceiling_exactly_is_not_truncated():
    """恰好填满安全界没有丢弃任何内容，不该被打上截断标注。"""
    g = UnitGate(6)
    out = g.feed("甲甲。乙乙。")
    assert out == ["甲甲。", "乙乙。"]
    assert g.flush() == []
    assert g.truncated is False


def test_gate_drops_content_arriving_after_the_ceiling():
    g = UnitGate(4)
    assert g.feed("甲甲。乙乙。") == ["甲甲。"]
    assert g.feed("丙。") == []
    assert g.truncated is True


def test_gate_stops_at_the_previous_sentence_when_the_next_does_not_fit():
    """整句放不下就在前一句收尾：宁可少说，也不出半句。"""
    g = UnitGate(6)
    out = g.feed("甲甲。") + g.feed("乙乙乙乙。")
    assert out == ["甲甲。"]
    assert g.flush() == []
    assert g.truncated is True


def test_gate_cuts_an_oversized_first_sentence_at_a_clause():
    """首句就超预算时退到子句界，而不是产出空正文。"""
    g = UnitGate(6)
    assert g.feed("甲甲甲，乙乙乙。") == ["甲甲甲，"]
    assert g.truncated is True
