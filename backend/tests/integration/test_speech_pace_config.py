def _post_agent(client, conn_id, kb_id, **over):
    body = {
        "name": "批判者",
        "model_connection_id": conn_id,
        "model_name": "qwen-max",
        "kb_ids": [kb_id],
        "persona": "默认共识里有漏洞",
        "expertise": "反例构造",
        "speaking_style": "直接质疑",
    }
    body.update(over)
    return client.post("/agents", json=body)


def test_create_agent_defaults_to_balanced(client, seeded):
    r = _post_agent(client, seeded["conn"]["conn_id"], seeded["kb"]["kb_id"])
    assert r.status_code == 201
    assert r.json()["speech_pace"] == "均衡"


def test_create_agent_accepts_explicit_pace(client, seeded):
    r = _post_agent(client, seeded["conn"]["conn_id"], seeded["kb"]["kb_id"], speech_pace="快闪")
    assert r.status_code == 201
    assert r.json()["speech_pace"] == "快闪"


def test_create_agent_rejects_unknown_pace(client, seeded):
    r = _post_agent(client, seeded["conn"]["conn_id"], seeded["kb"]["kb_id"], speech_pace="极速")
    assert r.status_code == 422


def test_patch_agent_changes_pace(client, seeded):
    aid = seeded["agent"]["agent_id"]
    r = client.patch(f"/agents/{aid}", json={"speech_pace": "深思"})
    assert r.status_code == 200
    assert r.json()["speech_pace"] == "深思"
    listed = {a["agent_id"]: a for a in client.get("/agents").json()}
    assert listed[aid]["speech_pace"] == "深思"


def test_patch_agent_without_pace_keeps_it(client, seeded):
    aid = seeded["agent"]["agent_id"]
    client.patch(f"/agents/{aid}", json={"speech_pace": "快闪"})
    r = client.patch(f"/agents/{aid}", json={"expertise": "技术债与演化"})
    assert r.json()["speech_pace"] == "快闪"


def test_response_flags_thinking_budget_support(client, seeded):
    """seeded 用 qwen-max，前缀规则判为支持思考分级。"""
    aid = seeded["agent"]["agent_id"]
    assert seeded["agent"]["pace_budget_supported"] is True
    assert client.get("/agents").json()[0]["pace_budget_supported"] is True
    patched = client.patch(f"/agents/{aid}", json={"expertise": "技术债"}).json()
    assert patched["pace_budget_supported"] is True


def test_response_flags_unsupported_model(client, seeded):
    cid = seeded["conn"]["conn_id"]
    client.patch(
        f"/model-connections/{cid}",
        json={"available_models": ["qwen-max", "deepseek-v4.1-flash"]},
    )
    r = _post_agent(
        client,
        cid,
        seeded["kb"]["kb_id"],
        name="成本分析师",
        model_name="deepseek-v4.1-flash",
    )
    assert r.status_code == 201
    assert r.json()["pace_budget_supported"] is False
