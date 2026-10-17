"""检索服务：本地 Chroma 检索 + 外部中心知识库组合检索（等权合并）。

组合口径（design-spec-connect-to-my-knowledge-lib 第 6 节）：
- 本地与各挂接的外部语料库并发查询（挂接即生效，解除即回到纯本地，无启停开关）；
- 各源按源内最大最小归一化到 [0,1] 后等权合并；
- 按内容哈希跨源去重（同片段留高分者），统一截断 top_k；
- 外部单源失败按 0 条处理并记入 degraded（显式降级，不静默）。
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from app.rag.vectorstore import VectorStore


@dataclass
class ChunkHit:
    kb_id: str
    chunk_id: str
    text: str
    score: float
    source: str = ""  # local:<知识库名> / ext:<别名>，仅日志与诊断，不进界面呈现


def _normalize(hits: list[ChunkHit]) -> list[ChunkHit]:
    """源内最大最小归一化到 [0,1]；空源返回空，单条/全同分取 1.0。"""
    if not hits:
        return []
    scores = [h.score for h in hits]
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [replace(h, score=1.0) for h in hits]
    return [replace(h, score=(h.score - lo) / (hi - lo)) for h in hits]


def _dedup(hits: list[ChunkHit]) -> list[ChunkHit]:
    """按内容哈希跨源去重，同片段保留高分者。"""
    best: dict[str, ChunkHit] = {}
    for h in hits:
        key = hashlib.sha1(h.text.strip()[:512].encode("utf-8")).hexdigest()
        if key not in best or h.score > best[key].score:
            best[key] = h
    return list(best.values())


class RetrievalService:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder,
        link_provider=None,
        ext_client_provider=None,
    ):
        self.vs = vector_store
        self.embedder = embedder
        # link_provider: Callable[[list[str]], list[ExternalCorpusLink]]
        # ext_client_provider: Callable[[], ExternalKbClient | None]
        self.link_provider = link_provider
        self.ext_client_provider = ext_client_provider

    def retrieve(self, query: str, kb_ids: list[str], top_k: int = 5) -> list[ChunkHit]:
        if not kb_ids:
            return []
        vector = self.embedder.embed([query])[0]
        hits: list[ChunkHit] = []
        for kb_id in kb_ids:
            for chunk_id, text, score in self.vs.query(kb_id, vector, top_k=top_k):
                hits.append(
                    ChunkHit(kb_id=kb_id, chunk_id=chunk_id, text=text, score=score, source=f"local:{kb_id}")
                )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def _external_hits(
        self, query: str, kb_ids: list[str], top_k: int
    ) -> tuple[list[ChunkHit], list[str]]:
        """收集各挂接外部语料库的命中；外部失败显式记入 degraded（按 0 条处理）。"""
        links = []
        if self.link_provider is not None and kb_ids:
            links = list(self.link_provider(kb_ids))  # 挂接即生效，无启停开关
        if not links:
            return [], []

        from app.integrations.external_kb import ExternalKbError

        client = self.ext_client_provider() if self.ext_client_provider is not None else None
        if client is None:
            # 连接未配置/未启用是主动关闭而非故障：纯本地、不记降级
            return [], []

        ext_hits: list[ChunkHit] = []
        degraded: list[str] = []
        with ThreadPoolExecutor(max_workers=min(len(links), 4)) as ex:
            futures = {ex.submit(client.search, l.ext_corpus_id, query, top_k): l for l in links}
        for fut, link in futures.items():
            try:
                rows = fut.result()
            except ExternalKbError as exc:
                degraded.append(f"{link.alias}:{exc.kind}")
                continue
            except Exception:
                degraded.append(f"{link.alias}:error")
                continue
            if not isinstance(rows, list):
                degraded.append(f"{link.alias}:bad_response")
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                text = r.get("content") if isinstance(r.get("content"), str) else ""
                if not text:
                    continue
                try:
                    score = float(r.get("score"))
                except (TypeError, ValueError):
                    continue
                seq = r.get("seq")
                rel = r.get("rel_path") or ""
                ext_hits.append(
                    ChunkHit(
                        kb_id=link.kb_id,
                        chunk_id=f"ext:{link.ext_corpus_id}:{rel}:{seq}",
                        text=text,
                        score=score,
                        source=f"ext:{link.alias}",
                    )
                )
        return ext_hits, degraded

    def _merge_equal(
        self, local: list[ChunkHit], ext_hits: list[ChunkHit], top_k: int
    ) -> list[ChunkHit]:
        """与发言完全同口径的合并：源内归一化 → 等权合并 → 内容哈希去重 → 截断。"""
        merged = _normalize(local) + _normalize(ext_hits)
        merged = _dedup(merged)
        merged.sort(key=lambda h: h.score, reverse=True)
        return merged[:top_k]

    def retrieve_debug(
        self, query: str, kb_ids: list[str], top_k: int = 5
    ) -> tuple[list[ChunkHit], list[ChunkHit], list[ChunkHit], list[str]]:
        """分组透出检索：返回 (本地命中, 外部命中, 合并结果, 降级源列表)。

        供管理后台「知识检索测试验证」使用：合并结果与发言上下文完全一致
        （同算法、同口径），测试为一次性内存态操作，不落库。
        """
        local = self.retrieve(query, kb_ids, top_k)
        ext_hits, degraded = self._external_hits(query, kb_ids, top_k)
        merged = self._merge_equal(local, ext_hits, top_k)
        return local, ext_hits, merged, degraded

    def retrieve_combined(
        self, query: str, kb_ids: list[str], top_k: int = 5
    ) -> tuple[list[ChunkHit], list[str]]:
        """组合检索：返回 (合并后的命中, 降级源列表)。

        与 retrieve_debug 共用同一算法路径（发言路径行为不变）。
        """
        _local, _ext, merged, degraded = self.retrieve_debug(query, kb_ids, top_k)
        return merged, degraded
