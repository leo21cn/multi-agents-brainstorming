def test_speech_records_source_and_cited_kbs(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    r = client.post(
        f"/rooms/{room['room_id']}/speak",
        json={"agent_id": seeded["agent"]["agent_id"], "intent": "延期根因"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["source_type"] == "智能体"
    assert set(body["cited_kb_ids"]) <= {seeded["kb"]["kb_id"]}


def test_speech_no_hit_marks_flag(client, seeded, make_room):
    empty_kb = client.post(
        "/knowledge-bases", json={"name": "空库", "dimension": "业务领域"}
    ).json()
    agent = client.post(
        "/agents",
        json={
            "name": "无命中智能体",
            "model_connection_id": seeded["conn"]["conn_id"],
            "model_name": "qwen-plus",
            "kb_ids": [empty_kb["kb_id"]],
            "role_card_id": seeded["card"]["card_id"],
        },
    ).json()
    room = make_room(agent["agent_id"])
    body = client.post(
        f"/rooms/{room['room_id']}/speak", json={"agent_id": agent["agent_id"]}
    ).json()
    assert body["no_kb_citation"] is True
