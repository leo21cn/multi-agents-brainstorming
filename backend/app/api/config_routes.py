from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.schemas import (
    AgentCreate,
    AgentUpdate,
    KnowledgeBaseCreate,
    ModelConnectionCreate,
    ModelConnectionUpdate,
    RoleCardCreate,
)
from app.deps import get_config_service
from app.domain.enums import KnowledgeDimension
from app.services.config_service import ConfigService, mask_key

router = APIRouter(tags=["config"])


def _conn_out(conn) -> dict:
    return {**conn.model_dump(exclude={"api_key"}), "api_key": mask_key(conn.api_key)}


# ---------- 模型连接 ----------
@router.post("/model-connections", status_code=201)
def create_connection(body: ModelConnectionCreate, svc: ConfigService = Depends(get_config_service)):
    return _conn_out(svc.create_connection(body))


@router.get("/model-connections")
def list_connections(svc: ConfigService = Depends(get_config_service)):
    return [_conn_out(c) for c in svc.list_connections()]


@router.patch("/model-connections/{conn_id}")
def update_connection(
    conn_id: str,
    body: ModelConnectionUpdate,
    svc: ConfigService = Depends(get_config_service),
):
    conn = svc.update_connection(conn_id, body)
    if conn is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return _conn_out(conn)


# ---------- 知识库与文档 ----------
@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(body: KnowledgeBaseCreate, svc: ConfigService = Depends(get_config_service)):
    try:
        kb = svc.create_knowledge_base(body.name, KnowledgeDimension(body.dimension))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return kb.model_dump()


@router.get("/knowledge-bases")
def list_knowledge_bases(svc: ConfigService = Depends(get_config_service)):
    return [k.model_dump() for k in svc.list_knowledge_bases()]


@router.post("/documents", status_code=201)
async def upload_document(
    kb_id: str = Form(...),
    file: UploadFile = File(...),
    svc: ConfigService = Depends(get_config_service),
):
    name = file.filename or "uploaded"
    ext = Path(name).suffix.lower()
    if ext not in {".md", ".txt"}:
        raise HTTPException(status_code=422, detail="unsupported format")
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="unsupported encoding")
    try:
        doc = svc.ingest_document(kb_id, name, ext.lstrip("."), text, size=len(raw))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return doc.model_dump()


@router.get("/documents")
def list_documents(kb_id: str | None = None, svc: ConfigService = Depends(get_config_service)):
    return [d.model_dump() for d in svc.list_documents(kb_id)]


# ---------- 角色卡 ----------
@router.post("/role-cards", status_code=201)
def create_role_card(body: RoleCardCreate, svc: ConfigService = Depends(get_config_service)):
    card = svc.create_role_card(body.name, body.persona, body.expertise, body.speaking_style)
    return card.model_dump()


@router.get("/role-cards")
def list_role_cards(svc: ConfigService = Depends(get_config_service)):
    return [c.model_dump() for c in svc.list_role_cards()]


# ---------- 智能体 ----------
@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate, svc: ConfigService = Depends(get_config_service)):
    try:
        agent = svc.create_agent(
            body.name, body.model_connection_id, body.model_name, body.kb_ids, body.role_card_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {**agent.model_dump(), "kb_ids": svc.bound_kb_ids(agent.agent_id)}


@router.get("/agents")
def list_agents(svc: ConfigService = Depends(get_config_service)):
    return [{**a.model_dump(), "kb_ids": svc.bound_kb_ids(a.agent_id)} for a in svc.list_agents()]


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: str, body: AgentUpdate, svc: ConfigService = Depends(get_config_service)
):
    try:
        agent = svc.update_agent(agent_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return {**agent.model_dump(), "kb_ids": svc.bound_kb_ids(agent.agent_id)}


# ---------- 初始化状态 ----------
@router.get("/system/init-status")
def init_status(svc: ConfigService = Depends(get_config_service)):
    return svc.init_status()
