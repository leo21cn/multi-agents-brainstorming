"""嵌入调用。默认提供方由运行期注入（种子脚本/服务），测试中可 monkeypatch。"""

from app.models.provider import ModelProvider

_provider: ModelProvider | None = None


def configure(provider: ModelProvider) -> None:
    global _provider
    _provider = provider


def embed_texts(texts: list[str]) -> list[list[float]]:
    if _provider is None:
        raise RuntimeError("embedding provider not configured")
    return _provider.embed(texts)
