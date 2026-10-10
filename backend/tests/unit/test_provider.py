from app.models.openai_compat import OpenAICompatProvider
from app.models.provider import ModelProvider


class FakeCompletions:
    def create(self, model, messages):  # noqa: D401, ANN001
        class R:
            pass

        r = R()
        r.choices = [type("C", (), {"message": type("M", (), {"content": "你好"})})]
        return r


class FakeClient:
    def __init__(self, *a, **k):  # noqa: ANN002, ANN003
        self.chat = type("X", (), {"completions": FakeCompletions()})()


def test_chat_returns_text():
    p = OpenAICompatProvider("http://x", "k", client_factory=FakeClient)
    assert p.chat("qwen-max", [{"role": "user", "content": "hi"}]) == "你好"


def test_provider_is_protocol():
    p = OpenAICompatProvider("http://x", "k", client_factory=FakeClient)
    assert isinstance(p, ModelProvider)
