from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import ModelConnectionCreate, ModelConnectionUpdate
from app.deps import get_config_service
from app.services.config_service import ConfigService, mask_key

router = APIRouter(prefix="/model-connections", tags=["config"])


def _out(conn) -> dict:
    return {**conn.model_dump(exclude={"api_key"}), "api_key": mask_key(conn.api_key)}


@router.post("", status_code=201)
def create_connection(
    body: ModelConnectionCreate, svc: ConfigService = Depends(get_config_service)
):
    return _out(svc.create_connection(body))


@router.get("")
def list_connections(svc: ConfigService = Depends(get_config_service)):
    return [_out(c) for c in svc.list_connections()]


@router.patch("/{conn_id}")
def update_connection(
    conn_id: str,
    body: ModelConnectionUpdate,
    svc: ConfigService = Depends(get_config_service),
):
    conn = svc.update_connection(conn_id, body)
    if conn is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return _out(conn)
