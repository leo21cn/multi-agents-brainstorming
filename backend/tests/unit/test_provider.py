from app.models.openai_compat import OpenAICompatProvider
from app.models.provider import CONTENT, END, ModelProvider


class FakeStreamCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, model, messages, **kw):  # noqa: ANN001, ANN002, ANN003
        self.kwargs = kw

        def chunk(text):
            return type("K", (), {"choices": [type("C", (), {"delta": type("D", (), {"content": text})})]})()

        # 含一个空 choices 的分片与一个 content 为 None 的分片，均应被跳过
        empty = type("K", (), {"choices": []})()
        nullish = type("K", (), {"choices": [type("C", (), {"delta": type("D", (), {"content": None})})]})()
        return iter([chunk("甲"), empty, chunk("乙"), nullish, chunk("丙")])


class FakeStreamClient:
    def __init__(self, *a, **k):  # noqa: ANN002, ANN003
        self.completions = FakeStreamCompletions()
        self.chat = type("X", (), {"completions": self.completions})()


def test_chat_returns_text():
    """一次性路径复用流式实现，正文即增量拼接结果。"""
    p = OpenAICompatProvider("http://x", "k", client_factory=FakeStreamClient)
    assert p.chat("qwen-max", [{"role": "user", "content": "hi"}]) == "甲乙丙"


def test_provider_is_protocol():
    p = OpenAICompatProvider("http://x", "k", client_factory=FakeStreamClient)
    assert isinstance(p, ModelProvider)


def test_stream_chat_yields_deltas_in_order():
    p = OpenAICompatProvider("http://x", "k", client_factory=FakeStreamClient)
    pieces = list(p.stream_chat("qwen-max", [{"role": "user", "content": "hi"}]))
    assert [q.text for q in pieces if q.kind == CONTENT] == ["甲", "乙", "丙"]
    assert pieces[-1].kind == END


def test_stream_chat_requests_stream_mode():
    client = FakeStreamClient()
    p = OpenAICompatProvider("http://x", "k", client_factory=lambda **kw: client)
    list(p.stream_chat("qwen-max", [{"role": "user", "content": "hi"}]))
    assert client.chat.completions.kwargs.get("stream") is True


def test_demo_provider_stream_matches_chat():
    from app.models.demo_provider import DemoProvider

    p = DemoProvider()
    messages = [{"role": "system", "content": "架构师。资深架构师"}, {"role": "user", "content": "延期两周怎么办"}]
    pieces = list(p.stream_chat("m", messages))
    assert "".join(q.text for q in pieces if q.kind == CONTENT) == p.chat("m", messages)
    assert len(pieces) > 1 and pieces[-1].kind == END


class FakeEmbeddings:
    def __init__(self):
        self.models = []

    def create(self, model, input):  # noqa: ANN001, ANN002
        self.models.append(model)
        return type("R", (), {"data": [type("D", (), {"embedding": [0.1]}) for _ in input]})()


class FakeEmbedClient:
    def __init__(self, *a, **k):  # noqa: ANN002, ANN003
        self.embeddings = FakeEmbeddings()


def test_embed_uses_default_model_when_unset():
    client = FakeEmbedClient()
    p = OpenAICompatProvider("http://x", "k", client_factory=lambda **kw: client)
    assert p.embed(["甲"]) == [[0.1]]
    assert client.embeddings.models == ["text-embedding-v3"]


def test_embed_uses_configured_model():
    client = FakeEmbedClient()
    p = OpenAICompatProvider("http://x", "k", "text-embedding-v4", client_factory=lambda **kw: client)
    p.embed(["甲", "乙"])
    assert client.embeddings.models == ["text-embedding-v4"]


def test_embed_blank_model_falls_back_to_default():
    client = FakeEmbedClient()
    p = OpenAICompatProvider("http://x", "k", "", client_factory=lambda **kw: client)
    p.embed(["甲"])
    assert client.embeddings.models == ["text-embedding-v3"]
