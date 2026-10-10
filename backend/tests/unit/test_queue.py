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
