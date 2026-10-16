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
            "persona": "p",
            "expertise": "e",
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
            "persona": "p",
            "expertise": "e",
        },
    )
    assert nobind.status_code == 422


def test_agent_requires_persona(client, seeded):
    r = client.post(
        "/agents",
        json={
            "name": "z",
            "model_connection_id": seeded["conn"]["conn_id"],
            "model_name": "qwen-max",
            "kb_ids": [seeded["kb"]["kb_id"]],
            "persona": "  ",
            "expertise": "e",
        },
    )
    assert r.status_code == 422


def test_agent_payload_carries_room_count(client, seeded, make_room):
    # 未选用时为 0；每被一个房间选用就加一
    assert client.get("/agents").json()[0]["room_count"] == 0
    make_room(seeded["agent"]["agent_id"])
    make_room(seeded["agent"]["agent_id"], host="赵")
    assert client.get("/agents").json()[0]["room_count"] == 2


def test_delete_agent_in_use_returns_chinese_409(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    agent_id = seeded["agent"]["agent_id"]

    r = client.delete(f"/agents/{agent_id}")

    assert r.status_code == 409
    assert r.json()["detail"] == "该智能体已被房间选用，无法删除"
    assert len(client.get("/agents").json()) == 1
    # 房间仍在且选用关系未动
    assert agent_id in client.get(f"/rooms/{rid}").json()["agent_ids"]


def test_delete_agent_unused_succeeds(client, seeded):
    second = client.post(
        "/agents",
        json={
            "name": "备用",
            "model_connection_id": seeded["conn"]["conn_id"],
            "model_name": "qwen-plus",
            "kb_ids": [seeded["kb"]["kb_id"]],
            "persona": "p",
            "expertise": "e",
        },
    ).json()
    r = client.delete(f"/agents/{second['agent_id']}")
    assert r.status_code == 200
    assert all(a["agent_id"] != second["agent_id"] for a in client.get("/agents").json())


def test_delete_connection_and_kb_in_use_chinese_409(client, seeded):
    r1 = client.delete(f"/model-connections/{seeded['conn']['conn_id']}")
    assert r1.status_code == 409
    assert r1.json()["detail"] == "该连接被智能体引用，无法删除"
    r2 = client.delete(f"/knowledge-bases/{seeded['kb']['kb_id']}")
    assert r2.status_code == 409
    assert r2.json()["detail"] == "该知识库已被智能体绑定，无法删除"
