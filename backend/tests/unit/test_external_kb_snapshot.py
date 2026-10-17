"""外部知识库连接层快照：刷新、状态映射与「曾出现后消失」。"""

import asyncio

import pytest

import app.deps as deps
from app.integrations.external_kb import ExternalKbError
from app.integrations.external_kb_snapshot import (
    ExternalKbSnapshot,
    refresh_snapshot,
    snapshot_singleton,
)


class _FakeClient:
    def __init__(self, corpora=None, error=None):
        self.corpora = corpora or []
        self.error = error

    def list_corpora(self):
        if self.error is not None:
            raise self.error
        return self.corpora


def test_snapshot_status_mapping_and_missing():
    s = ExternalKbSnapshot()
    assert s.get_corpus_status("c1") is None  # 从未见过 → None
    s.update(
        [
            {"corpus_id": "c1", "name": "A", "archived": False},
            {"corpus_id": "c2", "name": "B", "archived": True},
        ]
    )
    assert s.get_corpus_status("c1") == "active"
    assert s.get_corpus_status("c2") == "archived"
    snap = s.snapshot()
    assert snap["state"] == "ok" and snap["refreshed_at"] is not None
    # 外部删除 c1 后：曾在历次快照中出现 → missing
    s.update([{"corpus_id": "c2", "name": "B", "archived": True}])
    assert s.get_corpus_status("c1") == "missing"
    assert s.get_corpus_status("c3") is None


def test_refresh_ok(monkeypatch):
    monkeypatch.setattr(
        deps, "get_external_kb_client", lambda: _FakeClient([{"corpus_id": "c1", "archived": False}])
    )
    asyncio.run(refresh_snapshot())
    snap = snapshot_singleton.snapshot()
    assert snap["state"] == "ok"
    assert [c["corpus_id"] for c in snap["corpora"]] == ["c1"]


def test_refresh_error_keeps_old_snapshot(monkeypatch):
    monkeypatch.setattr(
        deps,
        "get_external_kb_client",
        lambda: _FakeClient(error=ExternalKbError("unreachable", "无法连接外部知识库：x")),
    )
    asyncio.run(refresh_snapshot())
    snap = snapshot_singleton.snapshot()
    assert snap["state"] == "error:unreachable"
    # 旧快照保留（上一条用例写入的 c1 仍在），错误显式
    assert snap["error"] is not None


def test_refresh_disabled_when_unconfigured(monkeypatch):
    monkeypatch.setattr(deps, "get_external_kb_client", lambda: None)
    asyncio.run(refresh_snapshot())
    assert snapshot_singleton.snapshot()["state"] == "disabled"
