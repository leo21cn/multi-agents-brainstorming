from pathlib import Path

from sqlalchemy import create_engine
from sqlmodel import SQLModel

from app.settings import Settings


def get_engine(settings: Settings):
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)
