from openai import OpenAI

from app.models.provider import ModelProvider


class OpenAICompatProvider:
    """OpenAI 兼容提供方实现，可用于 DashScope 兼容端点。"""

    def __init__(self, base_url: str, api_key: str, client_factory=OpenAI):
        self._client = client_factory(base_url=base_url, api_key=api_key)

    def chat(self, model_name: str, messages: list[dict], **kw) -> str:
        r = self._client.chat.completions.create(model=model_name, messages=messages, **kw)
        return r.choices[0].message.content

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = self._client.embeddings.create(model="text-embedding-v3", input=texts)
        return [d.embedding for d in r.data]
