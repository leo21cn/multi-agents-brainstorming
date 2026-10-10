from sqlmodel import Session, select

from app.domain.models import ModelConnection
from app.util import new_id


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return key[:3] + "•" * 8 + key[-4:]


class ConfigService:
    def __init__(self, engine):
        self.engine = engine

    def create_connection(self, data) -> ModelConnection:
        conn = ModelConnection(
            conn_id=new_id("conn"),
            name=data.name,
            provider_type=data.provider_type,
            base_url=data.base_url,
            api_key=data.api_key,
            available_models=list(data.available_models),
        )
        with Session(self.engine) as s:
            s.add(conn)
            s.commit()
            s.refresh(conn)
        return conn

    def list_connections(self) -> list[ModelConnection]:
        with Session(self.engine) as s:
            return list(s.exec(select(ModelConnection)).all())

    def update_connection(self, conn_id: str, data) -> ModelConnection | None:
        with Session(self.engine) as s:
            conn = s.get(ModelConnection, conn_id)
            if conn is None:
                return None
            for key, value in data.model_dump(exclude_none=True).items():
                setattr(conn, key, value)
            s.add(conn)
            s.commit()
            s.refresh(conn)
            return conn
