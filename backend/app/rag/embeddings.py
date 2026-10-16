"""嵌入调用。

提供方经工厂按次解析，故管理界面改动模型连接后无需重启即可生效；
未装工厂时回退到确定性哈希嵌入，保证离线可跑通。
"""

import hashlib
from typing import Callable

from app.models.provider import ModelProvider

_factory: Callable[[], ModelProvider | None] | None = None
_DIM = 64


def configure_factory(factory: Callable[[], ModelProvider | None] | None) -> None:
    global _factory
    _factory = factory


def hash_embedding(text: str) -> list[float]:
    vec = [0.0] * _DIM
    for ch in text:
        vec[int(hashlib.md5(ch.encode("utf-8")).hexdigest(), 16) % _DIM] += 1.0
    return vec


def embed_texts(texts: list[str]) -> list[list[float]]:
    provider = _factory() if _factory is not None else None
    if provider is not None:
        return provider.embed(texts)
    return [hash_embedding(t) for t in texts]


class EmbeddingAdapter:
    """供检索服务使用的嵌入适配器（转发到 embed_texts）。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts)
