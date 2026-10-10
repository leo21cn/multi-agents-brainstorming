"""嵌入调用。优先使用注入的提供方；未配置时回退到确定性哈希嵌入，保证离线可跑通。"""

import hashlib

from app.models.provider import ModelProvider

_provider: ModelProvider | None = None
_DIM = 64


def configure(provider: ModelProvider) -> None:
    global _provider
    _provider = provider


def hash_embedding(text: str) -> list[float]:
    vec = [0.0] * _DIM
    for ch in text:
        vec[int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16) % _DIM] += 1.0
    return vec


def embed_texts(texts: list[str]) -> list[list[float]]:
    if _provider is not None:
        return _provider.embed(texts)
    return [hash_embedding(t) for t in texts]


class EmbeddingAdapter:
    """供检索服务使用的嵌入适配器（转发到 embed_texts）。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts)
