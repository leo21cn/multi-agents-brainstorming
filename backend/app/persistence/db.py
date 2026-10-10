from sqlalchemy import create_engine
from sqlmodel import SQLModel

from app.settings import Settings


def get_engine(settings: Settings):
    return create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)
