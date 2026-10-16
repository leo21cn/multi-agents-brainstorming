from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from app.api.schemas import (
    AdminLogin,
    AdminTokenUpdate,
    AgentCreate,
    AgentUpdate,
    ConclusionModelSetting,
    KnowledgeBaseCreate,
    ModelConnectionCreate,
    ModelConnectionUpdate,
    UiOptions,
)
from app.deps import get_config_service
from app.domain.enums import KnowledgeDimension
from app.models.thinking import supports_thinking_budget
from app.services.config_service import ConfigService, InUseError, mask_key
from app.util import token_matches

router = APIRouter(tags=["config"])


def _agent_payload(svc: ConfigService, agent) -> dict:
    """智能体出参：附带绑定知识库、思考分级能力判定与被选用的房间数。

    能力判定是模型名的纯函数，由后端下发，前端不重复实现前缀表，避免两套判定口径；
    房间数供管理界面在删除前预告「使用中不可删」，避免必失败的请求。
    """
    return {
        **agent.model_dump(),
        "kb_ids": svc.bound_kb_ids(agent.agent_id),
        "pace_budget_supported": supports_thinking_budget(agent.model_name),
        "room_count": svc.rooms_using(agent.agent_id),
    }


def _conn_out(conn) -> dict:
    return {**conn.model_dump(exclude={"api_key"}), "api_key": mask_key(conn.api_key)}


# ---------- 管理员令牌 ----------
def require_admin_token(
    x_admin_token: str | None = Header(default=None),
    svc: ConfigService = Depends(get_config_service),
) -> None:
    if not token_matches(x_admin_token, svc.get_admin_token()):
        raise HTTPException(status_code=401, detail="invalid admin token")


@router.post("/admin/login")
def admin_login(body: AdminLogin, svc: ConfigService = Depends(get_config_service)):
    if not token_matches(body.token, svc.get_admin_token()):
        raise HTTPException(status_code=401, detail="invalid admin token")
    return {"ok": True}


@router.patch("/admin/token")
def update_admin_token(
    body: AdminTokenUpdate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        svc.set_admin_token(body.new_token)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True}


# ---------- 模型连接 ----------
@router.post("/model-connections", status_code=201)
def create_connection(
    body: ModelConnectionCreate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    return _conn_out(svc.create_connection(body))


@router.get("/model-connections")
def list_connections(
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    return [_conn_out(c) for c in svc.list_connections()]


@router.patch("/model-connections/{conn_id}")
def update_connection(
    conn_id: str,
    body: ModelConnectionUpdate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    conn = svc.update_connection(conn_id, body)
    if conn is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return _conn_out(conn)


@router.delete("/model-connections/{conn_id}")
def delete_connection(
    conn_id: str,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        ok = svc.delete_connection(conn_id)
    except InUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="connection not found")
    return {"ok": True}


# ---------- 知识库与文档 ----------
@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(
    body: KnowledgeBaseCreate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        kb = svc.create_knowledge_base(body.name, KnowledgeDimension(body.dimension))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return kb.model_dump()


@router.get("/knowledge-bases")
def list_knowledge_bases(svc: ConfigService = Depends(get_config_service)):
    return [k.model_dump() for k in svc.list_knowledge_bases()]


@router.delete("/knowledge-bases/{kb_id}")
def delete_knowledge_base(
    kb_id: str,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        ok = svc.delete_knowledge_base(kb_id)
    except InUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    return {"ok": True}


@router.post("/documents", status_code=201)
async def upload_document(
    kb_id: str = Form(...),
    file: UploadFile = File(...),
    _admin=Depends(require_admin_token),
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
def list_documents(
    kb_id: str | None = None,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    return [d.model_dump() for d in svc.list_documents(kb_id)]


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: str,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    if not svc.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="document not found")
    return {"ok": True}


# ---------- 智能体 ----------
@router.post("/agents", status_code=201)
def create_agent(
    body: AgentCreate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        agent = svc.create_agent(
            body.name,
            body.model_connection_id,
            body.model_name,
            body.kb_ids,
            body.persona,
            body.expertise,
            body.speaking_style,
            body.speech_pace,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _agent_payload(svc, agent)


@router.get("/agents")
def list_agents(svc: ConfigService = Depends(get_config_service)):
    return [_agent_payload(svc, a) for a in svc.list_agents()]


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: str,
    body: AgentUpdate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        agent = svc.update_agent(agent_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return _agent_payload(svc, agent)


@router.delete("/agents/{agent_id}")
def delete_agent(
    agent_id: str,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        ok = svc.delete_agent(agent_id)
    except InUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="agent not found")
    return {"ok": True}


# ---------- 初始化状态 ----------
@router.get("/system/init-status")
def init_status(svc: ConfigService = Depends(get_config_service)):
    return svc.init_status()


# ---------- 界面与行为选项 ----------
@router.get("/system/ui-options")
def get_ui_options(svc: ConfigService = Depends(get_config_service)):
    """开放只读：选项供房间页渲染控制面与决定是否自主举手；响应不携带访问令牌。"""
    return UiOptions(show_raise_sim=svc.get_show_raise_sim(), auto_raise=svc.get_auto_raise())


@router.patch("/system/ui-options")
def update_ui_options(
    body: UiOptions,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    svc.set_show_raise_sim(body.show_raise_sim)
    svc.set_auto_raise(body.auto_raise)
    return UiOptions(show_raise_sim=svc.get_show_raise_sim(), auto_raise=svc.get_auto_raise())


# ---------- 结论综合模型 ----------
@router.get("/system/conclusion-model")
def get_conclusion_model(
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    """只给管理界面读：房间页不需要知道结论由哪个模型综合，故不像界面选项那样开放。"""
    conn_id, model_name = svc.get_conclusion_model()
    return ConclusionModelSetting(conn_id=conn_id, model_name=model_name)


@router.patch("/system/conclusion-model")
def update_conclusion_model(
    body: ConclusionModelSetting,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        svc.set_conclusion_model(body.conn_id, body.model_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    conn_id, model_name = svc.get_conclusion_model()
    return ConclusionModelSetting(conn_id=conn_id, model_name=model_name)


# ---------- 语音转写模型 ----------
@router.get("/system/asr-model")
def get_asr_model(
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    """只给管理界面读：房间页不暴露转写模型取向，未配置时前端以提示引导。"""
    conn_id, model_name = svc.get_asr_model()
    return ConclusionModelSetting(conn_id=conn_id, model_name=model_name)


@router.patch("/system/asr-model")
def update_asr_model(
    body: ConclusionModelSetting,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    try:
        svc.set_asr_model(body.conn_id, body.model_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    conn_id, model_name = svc.get_asr_model()
    return ConclusionModelSetting(conn_id=conn_id, model_name=model_name)
