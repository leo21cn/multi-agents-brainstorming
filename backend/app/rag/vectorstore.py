import json
import logging
import math
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class VectorStore(Protocol):
    def add(self, kb_id: str, ids: list[str], texts: list[str], vectors: list[list[float]]) -> None: ...

    def query(self, kb_id: str, vector: list[float], top_k: int = 5) -> list[tuple[str, str, float]]: ...

    def delete(self, kb_id: str, ids: list[str]) -> None: ...


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class InMemoryVectorStore:
    """纯 Python 向量库，可持久化为 JSON；作为 Chroma 不可用时的回退。"""

    def __init__(self, path: str | None = None):
        self._path = path
        self._data: dict[str, list[tuple[str, str, list[float]]]] = {}
        if path and Path(path).exists():
            self._load()

    def add(self, kb_id, ids, texts, vectors) -> None:
        bucket = self._data.setdefault(kb_id, [])
        for i, t, v in zip(ids, texts, vectors):
            bucket.append((i, t, list(v)))
        self._persist()

    def query(self, kb_id, vector, top_k: int = 5):
        bucket = self._data.get(kb_id, [])
        scored = [(cid, text, _cosine(vector, vec)) for cid, text, vec in bucket]
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:top_k]

    def delete(self, kb_id, ids) -> None:
        drop = set(ids)
        self._data[kb_id] = [row for row in self._data.get(kb_id, []) if row[0] not in drop]
        self._persist()

    def _persist(self) -> None:
        if not self._path:
            return
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False)

    def _load(self) -> None:
        with open(self._path, encoding="utf-8") as f:
            self._data = json.load(f)


class ChromaVectorStore:
    """Chroma 后端（真实向量库）。距离越小越相近，此处转为相似度返回（越大越相近）。"""

    def __init__(self, path: str = "./data/chroma"):
        import chromadb

        self._client = chromadb.PersistentClient(path=path)

    def _col(self, kb_id: str):
        return self._client.get_or_create_collection(f"kb_{kb_id}")

    def add(self, kb_id, ids, texts, vectors) -> None:
        self._col(kb_id).upsert(ids=ids, documents=texts, embeddings=vectors)

    def query(self, kb_id, vector, top_k: int = 5):
        r = self._col(kb_id).query(query_embeddings=[vector], n_results=top_k)
        ids = r.get("ids", [[]])[0]
        docs = r.get("documents", [[]])[0]
        dists = r.get("distances", [[]])[0]
        return [(i, d, 1.0 - float(dist)) for i, d, dist in zip(ids, docs, dists)]

    def delete(self, kb_id, ids) -> None:
        if ids:
            self._col(kb_id).delete(ids=list(ids))


def default_vector_store(data_dir: str):
    """优先真实向量库 Chroma；仅在 chromadb 未安装时回退到内置实现。

    Chroma 打不开（目录被上一进程占用、路径不可写等）时让异常冒出，不降级：
    语料入库到 Chroma 后内置库是空的，静默降级会让检索恒返回零命中、发言全部
    标记为无知识库引用，而调用方看不到任何异常。
    """
    try:
        import chromadb  # noqa: F401
    except ImportError:
        logger.warning(
            "chromadb 未安装，回退到内置向量库（%s）；语料需重新入库后检索才有命中",
            Path(data_dir) / "vectors.json",
        )
        return InMemoryVectorStore(str(Path(data_dir) / "vectors.json"))
    return ChromaVectorStore(path=str(Path(data_dir) / "chroma"))
