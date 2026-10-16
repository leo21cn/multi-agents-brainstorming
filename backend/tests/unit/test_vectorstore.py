import logging
import sys

import pytest

from app.rag import vectorstore as vs_mod
from app.rag.vectorstore import InMemoryVectorStore, default_vector_store


def test_falls_back_to_inmemory_when_chromadb_missing(tmp_path, monkeypatch, caplog):
    """chromadb 未安装是文档化的离线路径，回退须留下可见日志。"""
    monkeypatch.setitem(sys.modules, "chromadb", None)

    with caplog.at_level(logging.WARNING):
        store = default_vector_store(str(tmp_path))

    assert isinstance(store, InMemoryVectorStore)
    assert "chromadb" in caplog.text


def test_open_failure_is_not_swallowed(tmp_path, monkeypatch):
    """Chroma 打不开（如目录被上一进程占用）不得静默降级为空库。

    内置库在语料入库到 Chroma 的场景下没有任何数据，降级会让检索恒返回零命中、
    发言全部标记为无知识库引用，且全程无异常无日志。
    """

    class Boom:
        def __init__(self, path=None):
            raise RuntimeError("chroma.sqlite3 is locked by another process")

    monkeypatch.setattr(vs_mod, "ChromaVectorStore", Boom)

    with pytest.raises(RuntimeError, match="locked"):
        default_vector_store(str(tmp_path))


def test_uses_chroma_when_available(tmp_path):
    store = default_vector_store(str(tmp_path))

    assert isinstance(store, vs_mod.ChromaVectorStore)
