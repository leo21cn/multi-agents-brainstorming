from dataclasses import dataclass

from app.rag.vectorstore import VectorStore


@dataclass
class ChunkHit:
    kb_id: str
    chunk_id: str
    text: str
    score: float


class RetrievalService:
    def __init__(self, vector_store: VectorStore, embedder):
        self.vs = vector_store
        self.embedder = embedder

    def retrieve(self, query: str, kb_ids: list[str], top_k: int = 5) -> list[ChunkHit]:
        if not kb_ids:
            return []
        vector = self.embedder.embed([query])[0]
        hits: list[ChunkHit] = []
        for kb_id in kb_ids:
            for chunk_id, text, score in self.vs.query(kb_id, vector, top_k=top_k):
                hits.append(ChunkHit(kb_id=kb_id, chunk_id=chunk_id, text=text, score=score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
