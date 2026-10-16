PAYLOAD = {
    "name": "DashScope 主",
    "provider_type": "OpenAI 兼容",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "sk-live-xxxxxx3f9a",
    "available_models": ["qwen-max", "qwen-plus"],
}


def test_create_then_list_masks_key(client):
    r = client.post("/model-connections", json=PAYLOAD)
    assert r.status_code == 201
    body = client.get("/model-connections").json()[0]
    assert "••••" in body["api_key"]
    assert body["name"] == "DashScope 主"
    assert body["available_models"] == ["qwen-max", "qwen-plus"]


def test_plaintext_key_never_returned(client):
    client.post("/model-connections", json=PAYLOAD)
    assert "sk-live" not in client.get("/model-connections").text
    assert "sk-live" not in client.post("/model-connections", json=PAYLOAD).text


def test_embedding_model_defaults_to_null_and_roundtrips(client):
    conn = client.post("/model-connections", json=PAYLOAD).json()
    assert conn["embedding_model"] is None

    updated = client.patch(
        f"/model-connections/{conn['conn_id']}", json={"embedding_model": "text-embedding-v4"}
    ).json()
    assert updated["embedding_model"] == "text-embedding-v4"
    assert client.get("/model-connections").json()[0]["embedding_model"] == "text-embedding-v4"


def test_embedding_model_accepts_explicit_value_on_create(client):
    body = client.post(
        "/model-connections", json={**PAYLOAD, "embedding_model": "text-embedding-v2"}
    ).json()
    assert body["embedding_model"] == "text-embedding-v2"


def test_embedding_model_blank_is_stored_as_null(client):
    conn = client.post(
        "/model-connections", json={**PAYLOAD, "embedding_model": "text-embedding-v4"}
    ).json()
    cleared = client.patch(
        f"/model-connections/{conn['conn_id']}", json={"embedding_model": "  "}
    ).json()
    assert cleared["embedding_model"] is None
