def test_agent_created_by_fixture(seeded):
    assert seeded["agent"]["agent_id"]
    assert seeded["agent"]["kb_ids"] == [seeded["kb"]["kb_id"]]


def test_agent_requires_known_model(client, seeded):
    bad = client.post(
        "/agents",
        json={
            "name": "x",
            "model_connection_id": seeded["conn"]["conn_id"],
            "model_name": "不存在",
            "kb_ids": [seeded["kb"]["kb_id"]],
            "role_card_id": seeded["card"]["card_id"],
        },
    )
    assert bad.status_code == 422


def test_agent_requires_knowledge_base(client, seeded):
    nobind = client.post(
        "/agents",
        json={
            "name": "y",
            "model_connection_id": seeded["conn"]["conn_id"],
            "model_name": "qwen-max",
            "kb_ids": [],
            "role_card_id": seeded["card"]["card_id"],
        },
    )
    assert nobind.status_code == 422
