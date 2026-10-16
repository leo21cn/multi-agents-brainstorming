from app.orchestration.conclusion_gate import CONCLUDING_DETAIL, ConclusionGate


def test_begin_claims_then_refuses_second_begin():
    gate = ConclusionGate()
    assert gate.begin("r1") is True
    assert gate.begin("r1") is False
    assert gate.active("r1") is True
    assert gate.active("r2") is False


def test_end_releases_the_room_only():
    gate = ConclusionGate()
    gate.begin("r1")
    gate.begin("r2")
    gate.end("r1")
    assert gate.active("r1") is False
    assert gate.active("r2") is True
    assert gate.begin("r1") is True


def test_rooms_are_independent():
    gate = ConclusionGate()
    assert gate.begin("r1") is True
    assert gate.begin("r2") is True
    gate.end("r2")
    assert gate.active("r1") is True
    assert gate.active("r2") is False


def test_reject_detail_is_the_shared_wording():
    assert CONCLUDING_DETAIL == "阶段结论产出生成中，请稍等"
