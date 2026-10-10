from app.rag.retrieval import RetrievalService


class FakeVS:
    def query(self, kb_id, vector, top_k):
        return [(f"{kb_id}-1", f"{kb_id} 文本", 0.1)]


class FakeEmbed:
    def embed(self, texts):
        return [[0.0] * 8]


def test_retrieve_limits_to_bound_kbs():
    svc = RetrievalService(FakeVS(), FakeEmbed())
    hits = svc.retrieve("延期根因", ["kb1", "kb2"])
    assert {h.kb_id for h in hits} == {"kb1", "kb2"}


def test_retrieve_empty_when_no_kb():
    svc = RetrievalService(FakeVS(), FakeEmbed())
    assert svc.retrieve("x", []) == []
