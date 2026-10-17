"""外部知识库：客户端错误分类与组合检索合并算法。"""

import pytest

from app.integrations.external_kb import ExternalKbClient, ExternalKbError
from app.rag.retrieval import ChunkHit, RetrievalService, _dedup, _normalize


class _Resp:
    def __init__(self, status_code, payload, bad_json=False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("invalid json")
        return self._payload


def test_client_list_corpora_and_search(monkeypatch):
    client = ExternalKbClient("http://127.0.0.1:8101", "tok")

    def fake_request(method, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer tok"
        if url.endswith("/api/v1/corpora"):
            return _Resp(200, {"data": [{"corpus_id": "c1", "name": "A"}]})
        if url.endswith("/api/v1/search"):
            return _Resp(200, {"data": [{"content": "片段", "score": 0.9}]})
        raise AssertionError(url)

    monkeypatch.setattr("app.integrations.external_kb.httpx.request", fake_request)
    assert client.list_corpora() == [{"corpus_id": "c1", "name": "A"}]
    assert client.search("c1", "q", 5) == [{"content": "片段", "score": 0.9}]


def test_client_error_taxonomy(monkeypatch):
    client = ExternalKbClient("http://x", "tok")

    def unauthorized(method, url, **kwargs):
        return _Resp(401, {"error": {"code": "AUTH_REQUIRED", "message": "x"}})

    monkeypatch.setattr("app.integrations.external_kb.httpx.request", unauthorized)
    with pytest.raises(ExternalKbError) as exc:
        client.list_corpora()
    assert exc.value.kind == "unauthorized"

    def bad_json(method, url, **kwargs):
        return _Resp(200, "not json", bad_json=True)

    monkeypatch.setattr("app.integrations.external_kb.httpx.request", bad_json)
    with pytest.raises(ExternalKbError) as exc:
        client.list_corpora()
    assert exc.value.kind == "bad_response"

    def connect_err(method, url, **kwargs):
        import httpx

        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.integrations.external_kb.httpx.request", connect_err)
    with pytest.raises(ExternalKbError) as exc:
        client.search("c1", "q", 5)
    assert exc.value.kind == "unreachable"


def test_client_get_corpus_not_found_returns_none(monkeypatch):
    client = ExternalKbClient("http://x", "tok")

    def not_found(method, url, **kwargs):
        return _Resp(404, {"detail": "CORPUS_NOT_FOUND"})

    monkeypatch.setattr("app.integrations.external_kb.httpx.request", not_found)
    assert client.get_corpus("nope") is None


def test_normalize_minmax_and_singleton():
    hits = [ChunkHit("k", "1", "a", 0.0), ChunkHit("k", "2", "b", 10.0)]
    norm = _normalize(hits)
    assert [h.score for h in norm] == [0.0, 1.0]
    single = _normalize([ChunkHit("k", "3", "c", 0.5)])
    assert single[0].score == 1.0
    assert _normalize([]) == []


def test_dedup_keeps_higher_score():
    a = ChunkHit("k1", "1", "同一段文字", 0.8)
    b = ChunkHit("k2", "2", "同一段文字", 0.3)
    kept = _dedup([a, b])
    assert len(kept) == 1 and kept[0].score == 0.8


class _FakeLink:
    def __init__(self, kb_id, ext_id, alias, enabled=True):
        self.kb_id = kb_id
        self.ext_corpus_id = ext_id
        self.alias = alias
        self.enabled = enabled


class _FakeClient:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error

    def search(self, corpus_id, query, top_k):
        if self.error is not None:
            raise self.error
        return self.rows


class _FakeVS:
    def query(self, kb_id, vector, top_k):
        return [("c1", "本地片段一", 0.9), ("c2", "本地片段二", 0.8)]


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


def _svc(links, client):
    return RetrievalService(
        _FakeVS(),
        _FakeEmbedder(),
        link_provider=lambda kb_ids: [l for l in links if l.kb_id in kb_ids],
        ext_client_provider=lambda: client,
    )


def test_combined_local_only_when_no_links():
    hits, degraded = _svc([], None).retrieve_combined("q", ["k1"], 5)
    assert degraded == []
    assert [h.chunk_id for h in hits] == ["c1", "c2"]


def test_combined_merges_external_and_local():
    link = _FakeLink("k1", "ext1", "中心库A")
    client = _FakeClient(rows=[{"content": "外部片段", "score": 1.0, "rel_path": "a.md", "seq": 0}])
    hits, degraded = _svc([link], client).retrieve_combined("q", ["k1"], 5)
    assert degraded == []
    sources = {h.source for h in hits}
    assert "ext:中心库A" in sources
    assert any(h.text == "外部片段" for h in hits)


def test_combined_external_failure_degraded_explicit():
    from app.integrations.external_kb import ExternalKbError

    link = _FakeLink("k1", "ext1", "中心库A")
    client = _FakeClient(error=ExternalKbError("unreachable", "无法连接外部知识库：x"))
    hits, degraded = _svc([link], client).retrieve_combined("q", ["k1"], 5)
    assert degraded == ["中心库A:unreachable"]
    assert [h.chunk_id for h in hits] == ["c1", "c2"]  # 本地照常


def test_combined_client_none_returns_local_silently():
    link = _FakeLink("k1", "ext1", "中心库A")
    hits, degraded = _svc([link], None).retrieve_combined("q", ["k1"], 5)
    assert degraded == []
    assert [h.chunk_id for h in hits] == ["c1", "c2"]


def test_retrieve_debug_matches_combined():
    """分组透出与发言合并同算法：merged 与 retrieve_combined 逐条一致。"""
    link = _FakeLink("k1", "ext1", "中心库A")
    client = _FakeClient(rows=[{"content": "外部片段", "score": 1.0, "rel_path": "a.md", "seq": 0}])
    svc = _svc([link], client)
    local, ext, merged, degraded = svc.retrieve_debug("q", ["k1"], 5)
    combined, deg2 = svc.retrieve_combined("q", ["k1"], 5)
    assert degraded == deg2 == []
    assert [(h.text, h.score, h.source) for h in merged] == [
        (h.text, h.score, h.source) for h in combined
    ]
    assert all(h.source.startswith("local:") for h in local)
    assert [h.source for h in ext] == ["ext:中心库A"]
    # 合并含外部片段
    assert any(h.text == "外部片段" for h in merged)


def test_retrieve_debug_external_failure_explicit():
    from app.integrations.external_kb import ExternalKbError

    link = _FakeLink("k1", "ext1", "中心库A")
    client = _FakeClient(error=ExternalKbError("unreachable", "无法连接外部知识库：x"))
    svc = _svc([link], client)
    local, ext, merged, degraded = svc.retrieve_debug("q", ["k1"], 5)
    assert degraded == ["中心库A:unreachable"]
    assert ext == []
    assert [h.chunk_id for h in local] == ["c1", "c2"]
    assert [h.text for h in merged] == ["本地片段一", "本地片段二"]


def test_retrieve_debug_no_links_no_client():
    svc = _svc([], None)
    local, ext, merged, degraded = svc.retrieve_debug("q", ["k1"], 5)
    assert degraded == [] and ext == []
    assert [h.text for h in merged] == ["本地片段一", "本地片段二"]
    assert len(local) == 2
