from functools import lru_cache

from app.persistence.db import get_engine, init_db
from app.services.config_service import ConfigService
from app.settings import Settings, load_settings


@lru_cache
def get_settings() -> Settings:
    return load_settings()


_engine = None


def get_engine_singleton():
    global _engine
    if _engine is None:
        _engine = get_engine(get_settings())
        init_db(_engine)
    return _engine


def get_config_service() -> ConfigService:
    return ConfigService(get_engine_singleton())
