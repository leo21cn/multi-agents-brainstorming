from app.orchestration.queue import RaiseHandQueue


def test_queue_order_and_release_dismiss():
    q = RaiseHandQueue()
    q.enqueue("r", "a1", "补充成本")
    q.enqueue("r", "a2", "质疑假设")
    items = q.list("r")
    assert [i.agent_id for i in items] == ["a1", "a2"]
    q.release("r", items[0].raise_id)
    assert [i.agent_id for i in q.list("r")] == ["a2"]
    q.dismiss("r", items[1].raise_id)
    assert q.list("r") == []


def test_enqueue_records_source_and_defaults_to_sim():
    q = RaiseHandQueue()

    assert q.enqueue("r1", "a1", "想说").source == "sim"
    assert q.enqueue("r1", "a2", "想说", source="auto").source == "auto"


def test_find_returns_the_hand_an_agent_holds():
    """同一智能体在活动队列中至多一条举手：每轮重判都要先认出自己那条。"""
    q = RaiseHandQueue()
    item = q.enqueue("r1", "a1")

    assert q.find("r1", "a1") is item
    assert q.find("r1", "a2") is None
    assert q.find("r2", "a1") is None

    q.dismiss("r1", item.raise_id)
    assert q.find("r1", "a1") is None


def test_refresh_rewrites_intent_keeping_position_and_id():
    """上下文变了，想说的话随之变；位置与标识不变，主持人才不必重新瞄准按钮。"""
    q = RaiseHandQueue()
    first = q.enqueue("r1", "a1", "旧意图甲", source="auto")
    second = q.enqueue("r1", "a2", "旧意图乙", source="auto")

    q.refresh("r1", first.raise_id, "新意图甲")

    items = q.list("r1")
    assert [i.raise_id for i in items] == [first.raise_id, second.raise_id]
    assert [i.intent for i in items] == ["新意图甲", "旧意图乙"]


def test_withdraw_marks_a_distinct_status():
    """智能体自己不再想说，与主持人驳回是两件事：状态须能分辨。"""
    from app.domain.enums import RaiseHandStatus

    q = RaiseHandQueue()
    item = q.enqueue("r1", "a1", "想说", source="auto")

    q.withdraw("r1", item.raise_id)

    assert q.list("r1") == []
    assert item.status == RaiseHandStatus.WITHDRAWN
    assert RaiseHandStatus.WITHDRAWN.value == "已撤回"


def test_a_hand_belongs_to_exactly_one_subject():
    """主体是智能体或人类成员，二者恰有一个非空：放行时据此分派。"""
    import pytest

    from app.orchestration.queue import RaiseHandQueue

    q = RaiseHandQueue()
    with pytest.raises(ValueError):
        q.enqueue("r1")
    with pytest.raises(ValueError):
        q.enqueue("r1", "a1", "想说", member_id="mem_1")


def test_member_hands_share_the_queue_and_keep_arrival_order():
    """主持人面对的就该是一条队列；分开呈现说不清「下一个该谁」。"""
    from app.orchestration.queue import SOURCE_MEMBER, RaiseHandQueue

    q = RaiseHandQueue()
    agent_hand = q.enqueue("r1", "a1", "智能体想说", source="auto")
    member_hand = q.enqueue("r1", None, "成员想说", SOURCE_MEMBER, member_id="mem_1")

    items = q.list("r1")
    assert [i.raise_id for i in items] == [agent_hand.raise_id, member_hand.raise_id]
    assert [i.source for i in items] == ["auto", "member"]
    assert SOURCE_MEMBER == "member"


def test_find_member_returns_the_hand_a_human_holds():
    from app.orchestration.queue import SOURCE_MEMBER, RaiseHandQueue

    q = RaiseHandQueue()
    q.enqueue("r1", "a1", "智能体想说", source="auto")
    mine = q.enqueue("r1", None, "成员想说", SOURCE_MEMBER, member_id="mem_1")

    assert q.find_member("r1", "mem_1") is mine
    assert q.find_member("r1", "mem_2") is None

    q.withdraw("r1", mine.raise_id)
    assert q.find_member("r1", "mem_1") is None
