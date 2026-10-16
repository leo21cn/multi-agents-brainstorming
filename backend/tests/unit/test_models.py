from sqlmodel import Session, SQLModel, create_engine, select

from app.domain.enums import KnowledgeDimension
from app.domain.models import Agent, AgentKnowledgeBase, KnowledgeBase


def test_agent_binds_multiple_kbs():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        kb1 = KnowledgeBase(kb_id="kb1", name="业务", dimension=KnowledgeDimension.BUSINESS)
        kb2 = KnowledgeBase(kb_id="kb2", name="方法", dimension=KnowledgeDimension.METHODOLOGY)
        ag = Agent(
            agent_id="a1",
            name="架构师",
            model_connection_id="c1",
            model_name="qwen-max",
            persona="资深架构师",
            expertise="技术债",
        )
        s.add_all([kb1, kb2, ag])
        s.commit()
        s.add_all(
            [
                AgentKnowledgeBase(agent_id="a1", kb_id="kb1"),
                AgentKnowledgeBase(agent_id="a1", kb_id="kb2"),
            ]
        )
        s.commit()
        rows = s.exec(select(AgentKnowledgeBase).where(AgentKnowledgeBase.agent_id == "a1")).all()
        assert {r.kb_id for r in rows} == {"kb1", "kb2"}


def test_all_tables_created():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    expected = {
        "model_connection",
        "knowledge_base",
        "document",
        "chunk",
        "agent",
        "agent_knowledge_base",
        "room",
        "room_agent",
        "human_member",
        "message",
        "raise_hand",
        "summary",
        "stance_mem",
        "memory_index",
        "conclusion_doc",
        "transcript_export",
    }
    assert expected <= set(SQLModel.metadata.tables.keys())
