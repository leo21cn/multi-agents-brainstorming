from functools import lru_cache

from app.memory.service import MemoryService
from app.persistence.db import get_engine, init_db
from app.rag.retrieval import RetrievalService
from app.rag.vectorstore import default_vector_store
from app.services.config_service import ConfigService
from app.services.room_service import RoomService
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


_vs = None


def get_vector_store_singleton():
    global _vs
    if _vs is None:
        _vs = default_vector_store(get_settings().data_dir)
    return _vs


def get_config_service() -> ConfigService:
    return ConfigService(get_engine_singleton(), get_vector_store_singleton())


def get_room_service() -> RoomService:
    return RoomService(get_engine_singleton(), get_config_service())


def get_memory_service() -> MemoryService:
    s = get_settings()
    return MemoryService(get_engine_singleton(), s.ctx_token_budget, s.summary_trigger_tokens)


def get_retrieval_service() -> RetrievalService:
    from app.rag.embeddings import EmbeddingAdapter

    return RetrievalService(get_vector_store_singleton(), EmbeddingAdapter())
