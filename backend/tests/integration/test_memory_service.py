from sqlmodel import SQLModel, create_engine

from app.domain.enums import SourceType
from app.memory.service import MemoryService


def _service() -> MemoryService:
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    return MemoryService(eng)


def test_stance_isolated_per_agent():
    svc = _service()
    svc.append_message("r1", SourceType.AGENT, "a1", "架构师观点：先降耦")
    svc.append_message("r1", SourceType.AGENT, "a2", "交付观点：看排期")
    svc.update_stance("r1", "a1", "先降耦、收缩范围")

    ctx_a1 = svc.build_context("命题", "r1", "a1")
    ctx_a2 = svc.build_context("命题", "r1", "a2")
    joined_a1 = " ".join(m["content"] for m in ctx_a1)
    joined_a2 = " ".join(m["content"] for m in ctx_a2)
    assert "先降耦、收缩范围" in joined_a1
    assert "先降耦、收缩范围" not in joined_a2


def test_maybe_summarize_triggers_over_threshold():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    svc = MemoryService(eng, summary_trigger_tokens=50)
    svc.append_message("r1", SourceType.HUMAN, "m1", "内容" * 30)
    row = svc.maybe_summarize("r1")
    assert row is not None
    assert svc.latest_summary("r1") == row.content
