from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.domain.enums import (
    DocumentStatus,
    KnowledgeDimension,
    MemberStatus,
    OrchestrationMode,
    RaiseHandStatus,
    RoomStatus,
    SourceType,
    SpeechPace,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 系统级实体 ----------

class SystemSetting(SQLModel, table=True):
    __tablename__ = "system_setting"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=_now)


class ModelConnection(SQLModel, table=True):
    __tablename__ = "model_connection"

    conn_id: str = Field(primary_key=True)
    name: str
    provider_type: str
    base_url: str
    api_key: str
    available_models: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # 空值表示使用提供方缺省嵌入模型；更换后既有向量与新查询向量不同空间，需重新入库文档
    embedding_model: str | None = None
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


class Agent(SQLModel, table=True):
    __tablename__ = "agent"

    agent_id: str = Field(primary_key=True)
    name: str
    model_connection_id: str = Field(foreign_key="model_connection.conn_id")
    model_name: str
    persona: str
    expertise: str
    speaking_style: Optional[str] = None
    speech_pace: SpeechPace = SpeechPace.BALANCED
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
    # 连接级身份的凭据：只在发放那一刻随响应出现一次，任何读接口都不下发。
    # 既有房间的成员行留空，须经管理令牌认领后才有可校验的身份。
    token: Optional[str] = None
    # 在线状态：退出聊天室时置已退出并清令牌，认领时恢复在线
    status: MemberStatus = Field(default=MemberStatus.ONLINE)
    joined_at: datetime = Field(default_factory=_now)


class Message(SQLModel, table=True):
    __tablename__ = "message"

    message_id: str = Field(primary_key=True)
    room_id: str = Field(foreign_key="room.room_id", index=True)
    source_type: SourceType
    source_id: str
    content: str
    # 受话者智能体标识列表；空列表表示群发。与 cited_kb_ids 同构存 JSON，不加外键约束，
    # 避免智能体删除时的级联问题
    addressee_agent_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # 受话的人类成员标识列表。与智能体列并存而不合并：智能体列的名字与校验口径都锚在
    # 阵容内智能体，还决定串行应答次序；成员列只决定发言权授予与界面标注。
    addressee_member_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    cited_kb_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    no_kb_citation: bool = False
    # 正文超出档位安全界被提前收束，落库内容没有说完；读者与导出都需要该标记，
    # 否则无从解释发言为何戛然而止。思考内容不落库，故不存在对应的思考截断标记。
    truncated: bool = False
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
