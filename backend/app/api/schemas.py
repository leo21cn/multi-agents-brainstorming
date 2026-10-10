from typing import Optional

from pydantic import BaseModel


# ---------- 模型连接 ----------
class ModelConnectionCreate(BaseModel):
    name: str
    provider_type: str
    base_url: str
    api_key: str
    available_models: list[str] = []


class ModelConnectionUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    available_models: Optional[list[str]] = None


# ---------- 知识库 / 角色卡 / 智能体 ----------
class KnowledgeBaseCreate(BaseModel):
    name: str
    dimension: str


class RoleCardCreate(BaseModel):
    name: str
    persona: str
    expertise: str
    speaking_style: Optional[str] = None


class AgentCreate(BaseModel):
    name: str
    model_connection_id: str
    model_name: str
    kb_ids: list[str]
    role_card_id: str


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    model_connection_id: Optional[str] = None
    model_name: Optional[str] = None
    kb_ids: Optional[list[str]] = None
    role_card_id: Optional[str] = None


# ---------- 房间 ----------
class RoomCreate(BaseModel):
    proposition: str
    orchestration_mode: str = "人类主持控制"
    agent_ids: list[str]
    host_name: str


class MemberCreate(BaseModel):
    display_name: str
