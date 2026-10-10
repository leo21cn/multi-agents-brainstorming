from typing import Protocol, runtime_checkable


@runtime_checkable
class ModelProvider(Protocol):
    def chat(self, model_name: str, messages: list[dict], **kw) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
