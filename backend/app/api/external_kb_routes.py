"""外部中心知识库：连接配置、连通探测、语料库列表、挂接关系 CRUD 与检索测试验证。

全部端点须管理令牌；外部令牌受保护——只回掩码、不下发明文。
管理类读接口（语料库列表、连通状态、挂接失效判定）走外部知识库连接层快照，
即时返回、不再逐条实时调外部；发言组合检索仍实时（见 rag/retrieval.py）。
检索测试验证为一次性内存态操作：与发言同算法、不落库、不影响房间讨论。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.config_routes import require_admin_token
from app.api.schemas import (
    ExternalKbSetting,
    ExternalKbTest,
    ExternalLinkCreate,
    ExternalLinkUpdate,
    RetrievalTestIn,
)
from app.deps import (
    get_config_service,
    get_external_kb_client,
    get_external_kb_snapshot,
    get_retrieval_service,
)
from app.integrations.external_kb import ExternalKbClient, ExternalKbError
from app.integrations.external_kb_snapshot import kick_snapshot_refresh
from app.services.config_service import AliasConflictError, ConfigService, mask_key

router = APIRouter(tags=["external-kb"])


def _link_payload(svc: ConfigService, snapshot, link) -> dict:
    """挂接出参：附加 stale（外部语料库已删/归档）标记，供界面呈现「外部已失效」。

    失效判定走连接层快照（即时、零外部调用）；快照无该语料库条目时按未失效处理
    （与既往「外部不可达时无从判定，按未失效处理」口径一致）。
    """
    payload = link.model_dump(mode="json")
    payload["stale"] = snapshot.get_corpus_status(link.ext_corpus_id) in ("archived", "missing")
    return payload


def _snap_error_detail(snap: dict) -> str:
    kind = snap["state"].split(":", 1)[1] if ":" in snap["state"] else snap["state"]
    return "外部知识库令牌无效" if kind == "unauthorized" else "无法连接外部知识库"


@router.get("/system/external-kb")
def get_external_kb_setting(
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    conf = svc.get_external_kb()
    return {
        "url": conf["url"],
        "token_masked": mask_key(conf["token"]) if conf["token"] else "",
        "enabled": conf["enabled"],
    }


@router.patch("/system/external-kb")
def update_external_kb_setting(
    body: ExternalKbSetting,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    conf = svc.get_external_kb()
    try:
        svc.set_external_kb(
            body.url if body.url is not None else conf["url"],
            body.token,
            body.enabled if body.enabled is not None else conf["enabled"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    kick_snapshot_refresh()  # 新配置尽快进连接层快照（线程安全，不等结果）
    conf = svc.get_external_kb()
    return {
        "url": conf["url"],
        "token_masked": mask_key(conf["token"]) if conf["token"] else "",
        "enabled": conf["enabled"],
    }


@router.get("/external-kb/healthz")
def external_kb_healthz(
    _admin=Depends(require_admin_token),
    client=Depends(get_external_kb_client),
    snapshot=Depends(get_external_kb_snapshot),
):
    """连接层快照健康状态（快）；POST 形态为表单当前值的实时测试。"""
    if client is None:
        raise HTTPException(status_code=422, detail="尚未配置外部知识库连接")
    snap = snapshot.snapshot()
    if snap["state"] == "ok":
        return {"ok": True, "state": "ok"}
    if snap["state"] == "disabled":
        # 配置刚启用、首刷未完成：按待同步处理，避免误报不可达
        return {"ok": True, "state": "pending"}
    raise HTTPException(status_code=502, detail=_snap_error_detail(snap))


@router.post("/external-kb/healthz")
def external_kb_healthz_test(
    body: ExternalKbTest,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    """用表单当前值即时测试连通（不落库）：缺省字段回退已保存配置。

    管理员在保存前就能验证刚填的地址与令牌；未填写且无已保存配置时 422。
    """
    saved = svc.get_external_kb()
    url = (body.url or "").strip().rstrip("/") or saved["url"]
    token = (body.token or "").strip() or saved["token"]
    if not url or not token:
        raise HTTPException(status_code=422, detail="尚未配置外部知识库连接")
    try:
        ExternalKbClient(url, token).healthz()
    except ExternalKbError as exc:
        detail = "外部知识库令牌无效" if exc.kind == "unauthorized" else "无法连接外部知识库"
        raise HTTPException(status_code=502, detail=detail)
    return {"ok": True}


@router.get("/external-kb/corpora")
def list_external_corpora(
    _admin=Depends(require_admin_token),
    client=Depends(get_external_kb_client),
    snapshot=Depends(get_external_kb_snapshot),
):
    """语料库列表走连接层快照：即时返回，附连接层状态供界面标注。"""
    if client is None:
        raise HTTPException(status_code=422, detail="尚未配置外部知识库连接")
    snap = snapshot.snapshot()
    return {
        "corpora": [
            {
                "corpus_id": c.get("corpus_id", ""),
                "name": c.get("name", ""),
                "subdir": c.get("subdir", ""),
                "archived": bool(c.get("archived")),
            }
            for c in snap["corpora"]
        ],
        "state": snap["state"],
        "refreshed_at": snap["refreshed_at"],
    }


@router.get("/knowledge-bases/{kb_id}/external-links")
def list_links(
    kb_id: str,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
    snapshot=Depends(get_external_kb_snapshot),
):
    if svc.get_knowledge_base(kb_id) is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    links = svc.list_external_links(kb_id)
    return {"links": [_link_payload(svc, snapshot, link) for link in links]}


@router.post("/knowledge-bases/{kb_id}/external-links", status_code=201)
def create_link(
    kb_id: str,
    body: ExternalLinkCreate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
    client=Depends(get_external_kb_client),
    snapshot=Depends(get_external_kb_snapshot),
):
    if svc.get_knowledge_base(kb_id) is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    if client is None:
        raise HTTPException(status_code=422, detail="尚未配置外部知识库连接")
    # 校验存在与归档：快照命中即按快照判；快照无该语料库（刚启用连接、首刷未完成）
    # 回落实时查询一次兜底，保证正确性。
    status = snapshot.get_corpus_status(body.ext_corpus_id)
    if status is None:
        try:
            corpus = client.get_corpus(body.ext_corpus_id)
        except ExternalKbError as exc:
            detail = "外部知识库令牌无效" if exc.kind == "unauthorized" else "无法连接外部知识库"
            raise HTTPException(status_code=502, detail=detail)
        if corpus is None:
            raise HTTPException(status_code=422, detail="外部语料库不存在")
        if bool(corpus.get("archived")):
            raise HTTPException(status_code=422, detail="外部语料库已归档，不能挂接")
    elif status == "missing":
        raise HTTPException(status_code=422, detail="外部语料库不存在")
    elif status == "archived":
        raise HTTPException(status_code=422, detail="外部语料库已归档，不能挂接")
    try:
        link = svc.add_external_link(kb_id, body.ext_corpus_id, body.alias)
    except AliasConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _link_payload(svc, snapshot, link)


@router.patch("/knowledge-bases/{kb_id}/external-links/{link_id}")
def update_link(
    kb_id: str,
    link_id: str,
    body: ExternalLinkUpdate,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
    snapshot=Depends(get_external_kb_snapshot),
):
    try:
        link = svc.update_external_link(kb_id, link_id, body.alias)
    except AliasConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if link is None:
        raise HTTPException(status_code=404, detail="external link not found")
    return _link_payload(svc, snapshot, link)


@router.delete("/knowledge-bases/{kb_id}/external-links/{link_id}")
def delete_link(
    kb_id: str,
    link_id: str,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
):
    if not svc.remove_external_link(kb_id, link_id):
        raise HTTPException(status_code=404, detail="external link not found")
    return {"ok": True}


def _hit_payload(hit) -> dict:
    return {
        "text": hit.text,
        "score": hit.score,
        "source": hit.source,
        "kb_id": hit.kb_id,
        "chunk_id": hit.chunk_id,
    }


@router.post("/external-kb/retrieval-test")
async def retrieval_test(
    body: RetrievalTestIn,
    _admin=Depends(require_admin_token),
    svc: ConfigService = Depends(get_config_service),
    service=Depends(get_retrieval_service),
    client=Depends(get_external_kb_client),
):
    """知识检索测试验证：与发言同算法的分组检索（本地／外部／合并），一次性内存态。

    范围：指定知识库＝该知识库本地文档 + 其挂接外部语料库；缺省＝全部知识库。
    外部失败显式降级标注；测试不落库、不触发任何生成路径。
    """
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="请输入查询内容")
    if len(query) > 200:
        raise HTTPException(status_code=422, detail="查询内容过长（最长 200 字）")
    if body.kb_id is not None:
        if svc.get_knowledge_base(body.kb_id) is None:
            raise HTTPException(status_code=404, detail="knowledge base not found")
        kb_ids = [body.kb_id]
    else:
        kb_ids = [kb.kb_id for kb in svc.list_knowledge_bases()]
        if not kb_ids:
            raise HTTPException(status_code=422, detail="尚无知识库")
    try:
        local, ext, merged, degraded = await asyncio.to_thread(
            service.retrieve_debug, query, kb_ids, 5
        )
    except Exception as exc:  # 嵌入端点不可达等
        raise HTTPException(status_code=502, detail=f"检索失败：{exc}")

    external_enabled = client is not None
    has_links = any(link.kb_id in kb_ids for link in svc.list_external_links())
    notes = list(degraded)
    if not external_enabled:
        notes.append("连接未启用")
    elif not has_links:
        notes.append("无挂接")
    return {
        "query": query,
        "kb_ids": kb_ids,
        "external_enabled": external_enabled,
        "local": [_hit_payload(h) for h in local],
        "external": [_hit_payload(h) for h in ext],
        "merged": [_hit_payload(h) for h in merged],
        "degraded": notes,
    }
