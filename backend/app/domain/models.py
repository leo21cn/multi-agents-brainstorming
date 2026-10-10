from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.domain.enums import (
    DocumentStatus,
    KnowledgeDimension,
    OrchestrationMode,
    RaiseHandStatus,
    RoomStatus,
    SourceType,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 系统级实体 ----------

class ModelConnection(SQLModel, table=True):
    __tablename__ = "model_connection"

    conn_id: str = Field(primary_key=True)
    name: str
    provider_type: str
    base_url: str
    api_key: str
    available_models: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_base"

    kb_id: str = Field(primary_key=True)
    name: str
    dimension: KnowledgeDimension
    created_at: datetime = Field(default_factory=_now)


class Document(SQLModel, table=True):
    __tablename__ = "document"

    doc_id: str = Field(primary_key=True)
    kb_id: str = Field(foreign_key="knowledge_base.kb_id")
    file_name: str
    format: str
    size: int
    status: DocumentStatus = DocumentStatus.PENDING
    file_ref: str
    uploaded_at: datetime = Field(default_factory=_now)


class Chunk(SQLModel, table=True):
    __tablename__ = "chunk"

    chunk_id: str = Field(primary_key=True)
    doc_id: str = Field(foreign_key="document.doc_id")
    seq: int
    text: str
    token_count: Optional[int] = None
    vector_ref: Optional[str] = None


class RoleCard(SQLModel, table=True):
    __tablename__ = "role_card"

    card_id: str = Field(primary_key=True)
    name: str
    persona: str
    expertise: str
    speaking_style: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Agent(SQLModel, table=True):
    __tablename__ = "agent"

    agent_id: str = Field(primary_key=True)
    name: str
    model_connection_id: str = Field(foreign_key="model_connection.conn_id")
    model_name: str
    role_card_id: str = Field(foreign_key="role_card.card_id")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class AgentKnowledgeBase(SQLModel, table=True):
    __tablename__ = "agent_knowledge_base"

    agent_id: str = Field(foreign_key="agent.agent_id", primary_key=True)
    kb_id: str = Field(foreign_key="knowledge_base.kb_id", primary_key=True)


# ---------- 房间级实体 ----------

class Room(SQLModel, table=True):
    __tablename__ = "room"

    room_id: str = Field(primary_key=True)
    proposition: str
    orchestration_mode: OrchestrationMode = OrchestrationMode.HUMAN_MODERATED
    status: RoomStatus = RoomStatus.PENDING
    host_member_id: Optional[str] = Field(default=None, foreign_key="human_member.member_id")
    created_at: datetime = Field(default_factory=_now)
    ended_at: Optional[datetime] = None


class RoomAgent(SQLModel, table=True):
    __tablename__ = "room_agent"

    room_id: str = Field(foreign_key="room.room_id", primary_key=True)
    agent_id: str = Field(foreign_key="agent.agent_id", primary_key=True)
    joined_at: datetime = Field(default_factory=_now)


class HumanMember(SQLModel, table=True):
    __tablename__ = "human_member"

    member_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id")
    display_name: str
    is_host: bool = False
    joined_at: datetime = Field(default_factory=_now)


class Message(SQLModel, table=True):
    __tablename__ = "message"

    message_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id", index=True)
    source_type: SourceType
    source_id: str
    content: str
    cited_kb_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    no_kb_citation: bool = False
    created_at: datetime = Field(default_factory=_now)


class RaiseHand(SQLModel, table=True):
    __tablename__ = "raise_hand"

    raise_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id")
    agent_id: str = Field(foreign_key="agent.agent_id")
    intent: Optional[str] = None
    status: RaiseHandStatus = RaiseHandStatus.QUEUED
    arrived_at: datetime = Field(default_factory=_now)


class Summary(SQLModel, table=True):
    __tablename__ = "summary"

    summary_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id")
    covered_from: str = Field(foreign_key="message.message_id")
    covered_to: str = Field(foreign_key="message.message_id")
    content: str
    token_count: Optional[int] = None
    created_at: datetime = Field(default_factory=_now)


class StanceMem(SQLModel, table=True):
    __tablename__ = "stance_mem"

    stance_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id")
    agent_id: str = Field(foreign_key="agent.agent_id")
    content: str
    covering: Optional[str] = None
    updated_at: datetime = Field(default_factory=_now)


class MemoryIndex(SQLModel, table=True):
    __tablename__ = "memory_index"

    index_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id")
    message_id: str = Field(foreign_key="message.message_id")
    vector_ref: Optional[str] = None


class ConclusionDoc(SQLModel, table=True):
    __tablename__ = "conclusion_doc"

    doc_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id")
    proposition: str
    sections: dict = Field(default_factory=dict, sa_column=Column(JSON))
    source_annotations: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    file_ref: str
    created_at: datetime = Field(default_factory=_now)


class TranscriptExport(SQLModel, table=True):
    __tablename__ = "transcript_export"

    export_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id")
    entry_count: int
    file_ref: str
    created_at: datetime = Field(default_factory=_now)
