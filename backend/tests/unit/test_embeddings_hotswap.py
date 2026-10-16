import sqlite3

from app.rag import embeddings

CONN = {
    "name": "DashScope 主",
    "provider_type": "OpenAI 兼容",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-live-xxxxxx3f9a",
    "available_models": ["qwen-max"],
}


class TagProvider:
    def __init__(self, tag: str):
        self.tag = tag

    def embed(self, texts):
        return [[float(len(self.tag))] for _ in texts]


def test_factory_resolved_on_every_call():
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return TagProvider("甲" if calls["n"] == 1 else "乙乙")

    embeddings.configure_factory(factory)
    try:
        assert embeddings.embed_texts(["x"]) == [[1.0]]
        assert embeddings.embed_texts(["x"]) == [[2.0]]
        assert calls["n"] == 2
    finally:
        embeddings.configure_factory(None)


def test_factory_returning_none_falls_back_to_hash():
    embeddings.configure_factory(lambda: None)
    try:
        assert embeddings.embed_texts(["甲"]) == [embeddings.hash_embedding("甲")]
    finally:
        embeddings.configure_factory(None)


def test_embedding_provider_follows_connection_change(env, client, monkeypatch):
    import app.models.openai_compat as oc
    from app import deps

    monkeypatch.setattr(deps, "get_config_service", lambda: env.config)
    monkeypatch.setattr(deps, "_emb_key", None)
    monkeypatch.setattr(deps, "_emb_provider", None)
    built = []

    class Spy:
        def __init__(
            self, base_url, api_key, embedding_model=oc.DEFAULT_EMBEDDING_MODEL, client_factory=None
        ):
            self.embedding_model = embedding_model
            built.append(embedding_model)

    monkeypatch.setattr(oc, "OpenAICompatProvider", Spy)

    conn = client.post("/model-connections", json=CONN).json()
    assert deps.embedding_provider_factory().embedding_model == "text-embedding-v3"

    # 改动连接的嵌入模型名后，无需重启即换用新配置
    client.patch(
        f"/model-connections/{conn['conn_id']}", json={"embedding_model": "text-embedding-v4"}
    )
    second = deps.embedding_provider_factory()
    assert second.embedding_model == "text-embedding-v4"
    assert built == ["text-embedding-v3", "text-embedding-v4"]

    # 键未变化时复用同一客户端
    assert deps.embedding_provider_factory() is second
    assert len(built) == 2


def test_embedding_provider_follows_base_url_and_key_change(env, client, monkeypatch):
    import app.models.openai_compat as oc
    from app import deps

    monkeypatch.setattr(deps, "get_config_service", lambda: env.config)
    monkeypatch.setattr(deps, "_emb_key", None)
    monkeypatch.setattr(deps, "_emb_provider", None)
    built = []

    class Spy:
        def __init__(
            self, base_url, api_key, embedding_model=oc.DEFAULT_EMBEDDING_MODEL, client_factory=None
        ):
            built.append((base_url, api_key))

    monkeypatch.setattr(oc, "OpenAICompatProvider", Spy)

    conn = client.post("/model-connections", json=CONN).json()
    deps.embedding_provider_factory()
    client.patch(f"/model-connections/{conn['conn_id']}", json={"api_key": "sk-live-yyyyyy1b2c"})
    deps.embedding_provider_factory()
    assert built == [
        (CONN["base_url"], "sk-live-xxxxxx3f9a"),
        (CONN["base_url"], "sk-live-yyyyyy1b2c"),
    ]


def test_embedding_provider_falls_back_to_demo_without_key(env, monkeypatch):
    from app import deps
    from app.models.demo_provider import DemoProvider

    monkeypatch.setattr(deps, "get_config_service", lambda: env.config)
    assert isinstance(deps.embedding_provider_factory(), DemoProvider)


def test_install_embedding_factory_wires_embed_texts(env, monkeypatch):
    from app import deps

    monkeypatch.setattr(deps, "get_config_service", lambda: env.config)
    deps.install_embedding_factory()
    try:
        # 无凭据连接 → 演示提供方 → 确定性哈希嵌入
        assert embeddings.embed_texts(["甲"]) == [embeddings.hash_embedding("甲")]
    finally:
        embeddings.configure_factory(None)


def test_migration_adds_column_and_is_idempotent(tmp_path):
    from scripts.migrate_embedding_model import migrate

    db = str(tmp_path / "m.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE model_connection (conn_id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO model_connection VALUES ('conn_1', '主')")
    conn.commit()
    conn.close()

    assert migrate(db).startswith("migrated: 1 connections")
    assert migrate(db) == "already migrated; skipped"

    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(model_connection)")]
    value = conn.execute("SELECT embedding_model FROM model_connection").fetchone()[0]
    conn.close()
    assert "embedding_model" in cols
    assert value is None


def test_migration_without_table_is_noop(tmp_path):
    from scripts.migrate_embedding_model import migrate

    db = str(tmp_path / "empty.db")
    sqlite3.connect(db).close()
    assert migrate(db) == "no model_connection table; nothing to do"
