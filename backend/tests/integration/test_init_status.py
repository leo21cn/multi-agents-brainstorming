def test_init_status_false_when_empty(client):
    assert client.get("/system/init-status").json()["ready"] is False


def test_init_status_true_after_seed(client, seeded):
    body = client.get("/system/init-status").json()
    assert body["ready"] is True
    assert body["kb_count"] >= 1
    assert body["bound_agent_count"] >= 1
