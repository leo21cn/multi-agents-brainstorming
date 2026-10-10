import json
import math
import os
from pathlib import Path
from typing import Protocol


class VectorStore(Protocol):
    def add(self, kb_id: str, ids: list[str], texts: list[str], vectors: list[list[float]]) -> None: ...

    def query(self, kb_id: str, vector: list[float], top_k: int = 5) -> list[tuple[str, str, float]]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class InMemoryVectorStore:
    """纯 Python 向量库，可持久化为 JSON。默认实现，避免依赖外部服务。"""

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
    """Chroma 后端；当 chromadb 可用时启用。"""

    def __init__(self, path: str = "./data/chroma"):
        import chromadb  # 延迟导入

        self._client = chromadb.PersistentClient(path=path)

    def _col(self, kb_id: str):
        return self._client.get_or_create_collection(f"kb_{kb_id}")

    def add(self, kb_id, ids, texts, vectors) -> None:
        self._col(kb_id).add(ids=ids, documents=texts, embeddings=vectors)

    def query(self, kb_id, vector, top_k: int = 5):
        r = self._col(kb_id).query(query_embeddings=[vector], n_results=top_k)
        return list(zip(r["ids"][0], r["documents"][0], r["distances"][0]))


def default_vector_store(data_dir: str) -> InMemoryVectorStore:
    return InMemoryVectorStore(os.path.join(data_dir, "vectors.json"))
