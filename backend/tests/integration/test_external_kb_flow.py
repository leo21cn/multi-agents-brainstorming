"""外部知识库：连接设置、连通探测、语料库列表、挂接 CRUD 与检索测试验证。"""

from fastapi.testclient import TestClient

from app.deps import get_external_kb_client, get_retrieval_service
from app.integrations.external_kb_snapshot import snapshot_singleton
from app.main import app


class FakeExtClient:
    def __init__(self, corpora=None):
        self.corpora = corpora or [
            {"corpus_id": "c1", "name": "中心库A", "subdir": "a", "archived": False},
            {"corpus_id": "c2", "name": "中心库B", "subdir": "b", "archived": False},
            {"corpus_id": "c3", "name": "归档库", "subdir": "c", "archived": True},
        ]

    def healthz(self):
        return True

    def list_corpora(self):
        return self.corpora

    def get_corpus(self, corpus_id):
        for c in self.corpora:
            if c["corpus_id"] == corpus_id:
                return c
        return None


_FAKE = FakeExtClient()


def _install_fake():
    app.dependency_overrides[get_external_kb_client] = lambda: _FAKE
    _FAKE.corpora = [
        {"corpus_id": "c1", "name": "中心库A", "subdir": "a", "archived": False},
        {"corpus_id": "c2", "name": "中心库B", "subdir": "b", "archived": False},
        {"corpus_id": "c3", "name": "归档库", "subdir": "c", "archived": True},
    ]
    # 管理读接口走连接层快照：与假客户端同步播种
    snapshot_singleton.update(_FAKE.corpora)


def test_external_kb_setting_round_trip(client, seeded):
    # 缺省未配置
    r = client.get("/system/external-kb")
    assert r.status_code == 200
    assert r.json() == {"url": "", "token_masked": "", "enabled": False}
    # 非法 URL → 422
    r = client.patch("/system/external-kb", json={"url": "ftp://x", "token": "tok", "enabled": True})
    assert r.status_code == 422
    # 启用但缺令牌 → 422
    r = client.patch(
        "/system/external-kb", json={"url": "http://127.0.0.1:8101", "token": None, "enabled": True}
    )
    assert r.status_code == 422
    # 成对保存：令牌掩码回显
    r = client.patch(
        "/system/external-kb",
        json={"url": "http://127.0.0.1:8101", "token": "secret-token-123456", "enabled": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "http://127.0.0.1:8101"
    assert body["token_masked"] != "" and "secret-token-123456" not in body["token_masked"]
    assert body["enabled"] is True
    # 令牌留空表示不修改
    r = client.patch("/system/external-kb", json={"url": "http://127.0.0.1:8101", "token": "", "enabled": True})
    assert r.status_code == 200 and r.json()["token_masked"] == body["token_masked"]
    # 无管理令牌 → 401（裸客户端，不带任何头）
    from fastapi.testclient import TestClient

    assert TestClient(app).get("/system/external-kb").status_code == 401
    # 复位
    r = client.patch("/system/external-kb", json={"url": "", "token": None, "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_healthz_and_corpora_require_connection(client):
    assert client.get("/external-kb/healthz").status_code == 422
    assert client.get("/external-kb/corpora").status_code == 422
    # 表单测试形态：未填写且无已保存配置 → 422
    assert client.post("/external-kb/healthz", json={"url": None, "token": None}).status_code == 422


def test_healthz_test_uses_form_values_without_saving(client, seeded):
    # 未保存配置，但表单填了值 → 用表单值即时测试（url 指向不可达端口证明走的是表单值）
    r = client.post("/external-kb/healthz", json={"url": "http://127.0.0.1:1", "token": "tok"})
    assert r.status_code == 502
    assert r.json()["detail"] == "无法连接外部知识库"
    # 确认未落库
    assert client.get("/system/external-kb").json()["url"] == ""


def test_corpora_list_and_healthz_with_fake(client):
    _install_fake()
    r = client.get("/external-kb/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["state"] == "ok"
    r = client.get("/external-kb/corpora")
    assert r.status_code == 200
    names = {c["name"] for c in r.json()["corpora"]}
    assert names == {"中心库A", "中心库B", "归档库"}
    assert r.json()["state"] == "ok" and r.json()["refreshed_at"] is not None


def test_link_crud_and_conflicts(client, seeded):
    _install_fake()
    kb_id = seeded["kb"]["kb_id"]
    kb_name = seeded["kb"]["name"]

    # 创建挂接
    r = client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "c1", "alias": "中心库甲"},
    )
    assert r.status_code == 201 and r.json()["alias"] == "中心库甲"
    link_id = r.json()["link_id"]

    # 重复挂接同一外部语料库 → 422
    r = client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "c1", "alias": "另一个别名"},
    )
    assert r.status_code == 422

    # 别名重复 → 409
    r = client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "c2", "alias": "中心库甲"},
    )
    assert r.status_code == 409

    # 与知识库名重名 → 409
    r = client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "c2", "alias": kb_name},
    )
    assert r.status_code == 409

    # 外部语料库不存在 → 422；已归档 → 422
    r = client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "nope", "alias": "幽灵"},
    )
    assert r.status_code == 422
    r = client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "c3", "alias": "归档库"},
    )
    assert r.status_code == 422

    # 列表带 stale 标记：c1 未归档 → stale False
    r = client.get(f"/knowledge-bases/{kb_id}/external-links")
    assert r.status_code == 200
    assert [l["alias"] for l in r.json()["links"]] == ["中心库甲"]
    assert r.json()["links"][0]["stale"] is False

    # 改别名（无启停开关）
    r = client.patch(
        f"/knowledge-bases/{kb_id}/external-links/{link_id}",
        json={"alias": "中心库甲改"},
    )
    assert r.status_code == 200 and r.json()["alias"] == "中心库甲改"
    assert "enabled" not in r.json()

    # 知识库不存在 → 404
    r = client.get("/knowledge-bases/nope/external-links")
    assert r.status_code == 404

    # 解除挂接
    r = client.delete(f"/knowledge-bases/{kb_id}/external-links/{link_id}")
    assert r.status_code == 200
    assert client.get(f"/knowledge-bases/{kb_id}/external-links").json()["links"] == []


def test_stale_flag_when_corpus_removed(client, seeded):
    _install_fake()
    kb_id = seeded["kb"]["kb_id"]
    r = client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "c1", "alias": "会消失的库"},
    )
    assert r.status_code == 201
    # 外部语料库消失：连接层快照刷新后不再包含 c1 → stale True（曾出现过）
    snapshot_singleton.update([])
    r = client.get(f"/knowledge-bases/{kb_id}/external-links")
    assert r.json()["links"][0]["stale"] is True


def _install_retrieval(env, ext_client_factory=None, links_provider=None):
    """以 env 的向量库与嵌入回退构造组合检索服务并覆盖依赖。"""
    from app.rag.embeddings import EmbeddingAdapter
    from app.rag.retrieval import RetrievalService

    def default_links(kb_ids):
        return [l for l in env.config.list_external_links() if l.kb_id in kb_ids]

    svc = RetrievalService(
        env.vs,
        EmbeddingAdapter(),
        link_provider=links_provider or default_links,
        ext_client_provider=ext_client_factory or (lambda: _FAKE),
    )
    app.dependency_overrides[get_retrieval_service] = lambda: svc
    return svc


def test_retrieval_test_requires_token_and_validates(client, seeded):
    # 无令牌 401
    assert TestClient(app).post(
        "/external-kb/retrieval-test", json={"query": "x"}
    ).status_code == 401
    # 查询为空 / 超长 422
    assert client.post("/external-kb/retrieval-test", json={"query": "   "}).status_code == 422
    assert client.post(
        "/external-kb/retrieval-test", json={"query": "长" * 201}
    ).status_code == 422
    # 知识库不存在 404
    assert client.post(
        "/external-kb/retrieval-test", json={"query": "x", "kb_id": "nope"}
    ).status_code == 404


def test_retrieval_test_groups(client, seeded, env):
    _install_fake()
    kb_id = seeded["kb"]["kb_id"]
    client.post(
        f"/knowledge-bases/{kb_id}/external-links",
        json={"ext_corpus_id": "c1", "alias": "中心库甲"},
    )
    _FAKE.search_rows = [{"content": "外部片段唯一", "score": 1.0, "rel_path": "a.md", "seq": 0}]

    class _SearchClient:
        def __init__(self, inner):
            self.inner = inner

        def search(self, corpus_id, query, top_k):
            return self.inner.search_rows

    _install_retrieval(env, ext_client_factory=lambda: _SearchClient(_FAKE))
    r = client.post(
        "/external-kb/retrieval-test", json={"query": "技术债", "kb_id": kb_id}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["external_enabled"] is True
    assert body["local"], "本地命中应为非空（种子文档已入库）"
    assert all(h["source"].startswith("local:") for h in body["local"])
    assert [h["text"] for h in body["external"]] == ["外部片段唯一"]
    assert body["external"][0]["source"] == "ext:中心库甲"
    assert any(h["text"] == "外部片段唯一" for h in body["merged"])
    assert body["degraded"] == []


def test_retrieval_test_disabled_and_no_links(client, seeded, env):
    kb_id = seeded["kb"]["kb_id"]
    # 连接未启用（假客户端 None）：外部组空、显式说明
    _install_retrieval(env, ext_client_factory=lambda: None)
    r = client.post("/external-kb/retrieval-test", json={"query": "技术债", "kb_id": kb_id})
    body = r.json()
    assert body["external_enabled"] is False
    assert body["external"] == []
    assert "连接未启用" in body["degraded"]
    assert body["local"], "本地命中照常"
    # 连接在但无挂接：说明项「无挂接」
    _install_fake()
    _install_retrieval(env)
    r = client.post("/external-kb/retrieval-test", json={"query": "技术债", "kb_id": kb_id})
    assert r.json()["external_enabled"] is True
    assert "无挂接" in r.json()["degraded"]
