from functools import lru_cache
from threading import Lock

from app.memory.service import MemoryService
from app.orchestration.conclusion_gate import ConclusionGate
from app.orchestration.floor import FloorGrant
from app.orchestration.queue import RaiseHandQueue
from app.orchestration.willingness import WillingnessService
from app.persistence.db import get_engine, init_db
from app.rag.retrieval import RetrievalService
from app.rag.vectorstore import default_vector_store
from app.services.conclusion_service import ConclusionService
from app.services.config_service import ConfigService
from app.services.room_service import RoomService
from app.services.speech_service import SpeechService
from app.settings import Settings, load_settings


@lru_cache
def get_settings() -> Settings:
    return load_settings()


_engine = None
_vs = None
# 懒建单例须持锁：同步端点跑在线程池里，冷启动时房间页并发拉三份配置，两个线程同时进
# Chroma 的 PersistentClient 会因其共享系统注册表的竞态抛异常，三个只读接口一起 500。
_lock = Lock()


def get_engine_singleton():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                engine = get_engine(get_settings())
                init_db(engine)
                _engine = engine
    return _engine


def get_vector_store_singleton():
    global _vs
    if _vs is None:
        with _lock:
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


def install_embedding_factory() -> None:
    """把嵌入提供方工厂装入嵌入模块，令连接改动无需重启即可生效。"""
    from app.rag import embeddings

    embeddings.configure_factory(embedding_provider_factory)


_emb_key: tuple | None = None
_emb_provider = None


def embedding_provider_factory():
    """按首个带密钥的连接返回嵌入提供方；无凭据时回退离线演示提供方。

    客户端以 (base_url, api_key, embedding_model) 为键缓存，键变化即重建，
    故管理界面改动连接后无需重启。
    """
    global _emb_key, _emb_provider

    conn = next((c for c in get_config_service().list_connections() if c.api_key), None)
    if conn is None:
        from app.models.demo_provider import DemoProvider

        return DemoProvider()
    from app.models.openai_compat import DEFAULT_EMBEDDING_MODEL, OpenAICompatProvider

    key = (conn.base_url, conn.api_key, conn.embedding_model or DEFAULT_EMBEDDING_MODEL)
    if key != _emb_key:
        _emb_key = key
        _emb_provider = OpenAICompatProvider(key[0], key[1], key[2])
    return _emb_provider


@lru_cache
def get_raise_hand_queue() -> RaiseHandQueue:
    return RaiseHandQueue()


@lru_cache
def get_floor() -> FloorGrant:
    return FloorGrant()


@lru_cache
def get_conclusion_gate() -> ConclusionGate:
    return ConclusionGate()


def _provider_for_conn(conn_id, config):
    conn = config.get_connection(conn_id)
    if conn and conn.api_key:
        from app.models.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(conn.base_url, conn.api_key)
    from app.models.demo_provider import DemoProvider

    return DemoProvider()


def _provider_factory(agent, config):
    return _provider_for_conn(agent.model_connection_id, config)


def get_speech_service() -> SpeechService:
    config = get_config_service()
    return SpeechService(
        get_engine_singleton(),
        config,
        get_memory_service(),
        get_retrieval_service(),
        lambda agent: _provider_factory(agent, config),
    )


def get_willingness_service() -> WillingnessService:
    """判定与发言共用同一个提供方工厂，故连接改动同样无需重启即生效。"""
    config = get_config_service()
    return WillingnessService(lambda agent: _provider_factory(agent, config))


_asr_key: tuple | None = None
_asr_provider = None


def get_asr_provider():
    """按语音转写设置（连接 + 模型名）返回转写提供方；未配置或无凭据时回落离线演示实现。

    设置改动即时生效：以 (base_url, api_key, model) 为键缓存，键变化即重建。
    """
    global _asr_key, _asr_provider

    conn_id, model = get_config_service().get_asr_model()
    if not conn_id or not model:
        from app.models.asr import DemoAsrProvider

        return DemoAsrProvider()
    conn = get_config_service().get_connection(conn_id)
    if conn is None or not conn.api_key:
        from app.models.asr import DemoAsrProvider

        return DemoAsrProvider()
    from app.models.asr import DashScopeStreamingAsrProvider

    key = (conn.base_url, conn.api_key, model)
    if key != _asr_key:
        _asr_key = key
        _asr_provider = DashScopeStreamingAsrProvider(conn.api_key, model)
    return _asr_provider


def get_conclusion_service() -> ConclusionService:
    """结论综合与发言、判定共用提供方工厂：综合目标按连接解析，连接改动无需重启。"""
    config = get_config_service()
    return ConclusionService(
        get_engine_singleton(),
        config,
        get_memory_service(),
        get_settings().data_dir,
        lambda conn_id: _provider_for_conn(conn_id, config),
    )
