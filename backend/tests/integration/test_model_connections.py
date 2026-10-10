from fastapi.testclient import TestClient

from app.main import app

PAYLOAD = {
    "name": "DashScope 主",
    "provider_type": "OpenAI 兼容",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-live-xxxxxx3f9a",
    "available_models": ["qwen-max", "qwen-plus"],
}


def test_create_then_list_masks_key():
    c = TestClient(app)
    r = c.post("/model-connections", json=PAYLOAD)
    assert r.status_code == 201
    body = c.get("/model-connections").json()[0]
    assert "••••" in body["api_key"]
    assert body["name"] == "DashScope 主"
    assert body["available_models"] == ["qwen-max", "qwen-plus"]


def test_plaintext_key_never_returned():
    c = TestClient(app)
    c.post("/model-connections", json=PAYLOAD)
    assert "sk-live" not in c.get("/model-connections").text
    assert "sk-live" not in c.post("/model-connections", json=PAYLOAD).text
