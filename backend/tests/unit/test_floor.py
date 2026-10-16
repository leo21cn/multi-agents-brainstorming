from app.orchestration.floor import FloorGrant


def test_grant_then_consume_yields_exactly_one_message():
    """一次放行只兑一条发言：不消耗就退化成「一旦获准便可以一直说」。"""
    f = FloorGrant()
    f.grant("r1", "mem_1")

    assert f.holds("r1", "mem_1")
    f.consume("r1", "mem_1")
    assert not f.holds("r1", "mem_1")


def test_consume_without_a_grant_is_harmless():
    f = FloorGrant()

    f.consume("r1", "mem_1")

    assert f.holders("r1") == []


def test_holders_are_room_scoped_and_sorted():
    """集合要能整体下发给界面，故次序须与授予先后无关。"""
    f = FloorGrant()
    f.grant("r1", "mem_b")
    f.grant("r1", "mem_a")
    f.grant("r2", "mem_c")

    assert f.holders("r1") == ["mem_a", "mem_b"]
    assert f.holders("r2") == ["mem_c"]
    assert f.holders("r3") == []


def test_revoke_takes_back_an_unspent_grant():
    f = FloorGrant()
    f.grant("r1", "mem_1")

    f.revoke("r1", "mem_1")

    assert not f.holds("r1", "mem_1")


def test_clear_drops_a_whole_room():
    """房间结束时一次性作废，否则会留下永不兑现的发言权。"""
    f = FloorGrant()
    f.grant("r1", "mem_1")
    f.grant("r1", "mem_2")

    f.clear("r1")

    assert f.holders("r1") == []
