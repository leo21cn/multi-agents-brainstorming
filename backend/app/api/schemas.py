from typing import Optional

from pydantic import BaseModel

from app.domain.enums import SpeechPace


# ---------- 管理员令牌 ----------
class AdminLogin(BaseModel):
    token: str


class AdminTokenUpdate(BaseModel):
    new_token: str


# ---------- 系统界面与行为选项 ----------
class UiOptions(BaseModel):
    show_raise_sim: bool = True
    auto_raise: bool = True


# ---------- 结论综合模型 ----------
class ConclusionModelSetting(BaseModel):
    conn_id: Optional[str] = None
    model_name: Optional[str] = None


# ---------- 模型连接 ----------
class ModelConnectionCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str
    api_key: str
    available_models: list[str] = []
    embedding_model: Optional[str] = None


class ModelConnectionUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    available_models: Optional[list[str]] = None
    embedding_model: Optional[str] = None


# ---------- 知识库 / 智能体 ----------
class KnowledgeBaseCreate(BaseModel):
    name: str
    dimension: str


class AgentCreate(BaseModel):
    name: str
    model_connection_id: str
    model_name: str
    kb_ids: list[str]
    persona: str
    expertise: str
    speaking_style: Optional[str] = None
    speech_pace: Optional[SpeechPace] = None


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    model_connection_id: Optional[str] = None
    model_name: Optional[str] = None
    kb_ids: Optional[list[str]] = None
    persona: Optional[str] = None
    expertise: Optional[str] = None
    speaking_style: Optional[str] = None
    speech_pace: Optional[SpeechPace] = None


# ---------- 房间 ----------
class RoomCreate(BaseModel):
    proposition: str
    orchestration_mode: str = "人类主持控制"
    agent_ids: list[str]
    host_name: str


class JoinRequest(BaseModel):
    display_name: str


class ClaimRequest(BaseModel):
    member_id: str
    admin_token: str


class HostAssign(BaseModel):
    member_id: str


class HumanMessageIn(BaseModel):
    content: str
    addressee_agent_ids: list[str] = []
    addressee_member_ids: list[str] = []


class SpeakRequest(BaseModel):
    agent_id: str
    intent: Optional[str] = None
    directed_from: Optional[str] = None


class RaiseHandRequest(BaseModel):
    agent_id: str
    intent: Optional[str] = None


class SelfRaise(BaseModel):
    intent: Optional[str] = None
