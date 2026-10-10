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
    assert (
        client.post(f"/rooms/{room['room_id']}/members", json={"display_name": "陈"}).status_code
        == 422
    )
    assert (
        client.post(f"/rooms/{room['room_id']}/members", json={"display_name": "林"}).status_code
        == 201
    )
