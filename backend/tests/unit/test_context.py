from app.memory.context import assemble_context


def test_context_respects_budget_and_order():
    msgs = [{"role": "assistant", "content": "x" * 100} for _ in range(50)]
    ctx = assemble_context("命题", "摘要", msgs, "立场", [], budget=200)
    assert sum(len(m["content"]) for m in ctx) <= 200
    assert ctx[0]["content"].startswith("命题")
