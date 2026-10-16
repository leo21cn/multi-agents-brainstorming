def _join(client, rid, name):
    return client.post(f"/rooms/{rid}/join", json={"display_name": name})


def test_creating_a_room_returns_the_host_token_once(client, seeded):
    """建房即发放主持人身份：令牌只在这一处出现，此后任何读接口都不下发。"""
    room = client.post(
        "/rooms",
        json={
            "proposition": "p",
            "orchestration_mode": "人类主持控制",
            "agent_ids": [seeded["agent"]["agent_id"]],
            "host_name": "陈",
        },
    ).json()

    assert room["host_member_id"]
    assert len(room["host_token"]) == 32
    listed = client.get(f"/rooms/{room['room_id']}/members").json()
    assert all("token" not in m for m in listed)


def test_join_registers_a_member_and_issues_a_token(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]

    r = _join(client, rid, "林工")

    assert r.status_code == 201
    body = r.json()
    assert body["display_name"] == "林工" and body["is_host"] is False
    assert len(body["token"]) == 32
    assert "token" not in client.get(f"/rooms/{rid}/members").json()[1]
    names = [m["display_name"] for m in client.get(f"/rooms/{rid}/members").json()]
    assert names == ["陈", "林工"]


def test_join_refuses_a_duplicate_nickname(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])

    r = _join(client, room["room_id"], "陈")

    assert r.status_code == 422
    assert r.json()["detail"] == "昵称已被占用，请换一个"


def test_join_refuses_a_nickname_that_collides_with_an_agent(client, seeded, make_room):
    """与阵容内智能体同名会让 @ 解析出现二义，故在进房时一次拒绝。"""
    room = make_room(seeded["agent"]["agent_id"])

    r = _join(client, room["room_id"], "架构师")

    assert r.status_code == 422
    assert r.json()["detail"] == "昵称已被占用，请换一个"


def test_join_refuses_after_the_room_ended(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(f"/rooms/{room['room_id']}/start")
    client.post(f"/rooms/{room['room_id']}/end")

    r = _join(client, room["room_id"], "林工")

    assert r.status_code == 422
    assert r.json()["detail"] == "房间已结束，不再接受新成员加入"


def test_join_reports_a_missing_room(client):
    assert _join(client, "room_nope", "林工").status_code == 404


def test_claim_reissues_a_token_for_an_existing_member(client, seeded, make_room):
    """既有房间的成员行令牌为空，主持人换了浏览器就靠认领找回身份。"""
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    host_id = room["host_member_id"]

    r = client.post(f"/rooms/{rid}/claim", json={"member_id": host_id, "admin_token": "111111"})

    assert r.status_code == 200
    body = r.json()
    assert body["member_id"] == host_id and body["is_host"] is True
    assert len(body["token"]) == 32
    assert "token" not in client.get(f"/rooms/{rid}/members").json()[0]


def test_claim_refuses_a_wrong_admin_token(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])

    r = client.post(
        f"/rooms/{room['room_id']}/claim",
        json={"member_id": room["host_member_id"], "admin_token": "999999"},
    )

    assert r.status_code == 403


def test_claim_refuses_a_member_of_another_room(client, seeded, make_room):
    first = make_room(seeded["agent"]["agent_id"])
    second = make_room(seeded["agent"]["agent_id"], host="赵")

    r = client.post(
        f"/rooms/{first['room_id']}/claim",
        json={"member_id": second["host_member_id"], "admin_token": "111111"},
    )

    assert r.status_code == 422
