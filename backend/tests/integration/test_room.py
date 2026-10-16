def test_room_rejected_without_init(client):
    r = client.post(
        "/rooms",
        json={
            "proposition": "p",
            "orchestration_mode": "人类主持控制",
            "agent_ids": ["a"],
            "host_name": "陈",
        },
    )
    assert r.status_code == 422


def test_room_requires_nonempty_roster(client, seeded):
    empty = client.post(
        "/rooms",
        json={
            "proposition": "p",
            "orchestration_mode": "人类主持控制",
            "agent_ids": [],
            "host_name": "陈",
        },
    )
    assert empty.status_code == 422
    ok = client.post(
        "/rooms",
        json={
            "proposition": "p",
            "orchestration_mode": "人类主持控制",
            "agent_ids": [seeded["agent"]["agent_id"]],
            "host_name": "陈",
        },
    )
    assert ok.status_code == 201


def test_room_member_unique_name(client, seeded):
    room = client.post(
        "/rooms",
        json={
            "proposition": "p",
            "orchestration_mode": "人类主持控制",
            "agent_ids": [seeded["agent"]["agent_id"]],
            "host_name": "陈",
        },
    ).json()
    rid = room["room_id"]
    assert client.post(f"/rooms/{rid}/join", json={"display_name": "陈"}).status_code == 422
    assert client.post(f"/rooms/{rid}/join", json={"display_name": "林"}).status_code == 201


def test_room_proposition_limited_to_200_chars(client, seeded):
    def post(prop):
        return client.post(
            "/rooms",
            json={
                "proposition": prop,
                "orchestration_mode": "人类主持控制",
                "agent_ids": [seeded["agent"]["agent_id"]],
                "host_name": "陈",
            },
        )

    too_long = post("问" * 201)
    assert too_long.status_code == 422
    assert too_long.json()["detail"] == "命题过长，请控制在 200 字以内"
    # 恰好 200 字与带首尾空白的 200 字内容都放行
    assert post("问" * 200).status_code == 201
    assert post("  " + "问" * 198 + "  ").status_code == 201
    # 越界的请求不落库：房间列表里只有上面两间
    rooms = client.get("/rooms").json()
    assert len(rooms) == 2
