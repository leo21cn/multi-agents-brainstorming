"""人类成员状态位（在线／已退出）：退出为服务端动作，配套 @ 与设为主持的强制规则。"""


def _join_member(client, rid, name):
    return client.post(f"/rooms/{rid}/join", json={"display_name": name}).json()


def test_leave_marks_member_left_and_invalidates_token(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    m = _join_member(client, rid, "林工")
    mc = client.as_member(m["member_id"], m["token"])

    assert mc.post(f"/rooms/{rid}/leave").status_code == 200

    members = client.get(f"/rooms/{rid}/members").json()
    lin = next(x for x in members if x["member_id"] == m["member_id"])
    assert lin["status"] == "已退出"
    # 旧令牌失效：REST 一律 401
    assert mc.post(f"/rooms/{rid}/messages", json={"content": "x"}).status_code == 401


def test_host_cannot_leave_while_room_not_ended(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")

    r = client.post(f"/rooms/{rid}/leave")

    assert r.status_code == 422
    assert "移交主持" in r.json()["detail"]


def test_host_leaves_after_transferring(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    m = _join_member(client, rid, "林工")

    assert client.post(f"/rooms/{rid}/host", json={"member_id": m["member_id"]}).status_code == 200
    assert client.post(f"/rooms/{rid}/leave").status_code == 200

    members = client.get(f"/rooms/{rid}/members").json()
    new_host = next(x for x in members if x["member_id"] == m["member_id"])
    assert new_host["is_host"] is True and new_host["status"] == "在线"


def test_only_human_ends_room_then_leaves(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")

    assert client.post(f"/rooms/{rid}/end").status_code == 200
    assert client.post(f"/rooms/{rid}/leave").status_code == 200

    assert client.get(f"/rooms/{rid}").json()["status"] == "已结束"


def test_claim_reactivates_a_left_member(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    m = _join_member(client, rid, "林工")
    client.as_member(m["member_id"], m["token"]).post(f"/rooms/{rid}/leave")

    r = client.post(
        f"/rooms/{rid}/claim", json={"member_id": m["member_id"], "admin_token": "111111"}
    )

    assert r.status_code == 200
    assert r.json()["status"] == "在线"
    # 新令牌可用：认领即回到房间
    nc = client.as_member(m["member_id"], r.json()["token"])
    assert nc.post(f"/rooms/{rid}/raise-hand/self", json={"intent": "补充"}).status_code == 201


def test_left_member_cannot_be_made_host(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    m = _join_member(client, rid, "林工")
    client.as_member(m["member_id"], m["token"]).post(f"/rooms/{rid}/leave")

    r = client.post(f"/rooms/{rid}/host", json={"member_id": m["member_id"]})

    assert r.status_code == 422
    assert "已退出" in r.json()["detail"]


def test_mention_of_left_member_is_rejected_and_not_stored(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    m = _join_member(client, rid, "林工")
    client.as_member(m["member_id"], m["token"]).post(f"/rooms/{rid}/leave")

    r = client.post(
        f"/rooms/{rid}/messages",
        json={"content": "@林工 你怎么看", "addressee_member_ids": [m["member_id"]]},
    )

    assert r.status_code == 422
    assert "已退出聊天室，不在线" in r.json()["detail"]
    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert not msgs


def test_ws_mention_of_left_member_is_rejected(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    m = _join_member(client, rid, "林工")
    client.as_member(m["member_id"], m["token"]).post(f"/rooms/{rid}/leave")

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json(
            {"type": "human_message", "content": "@林工 你怎么看", "addressee_member_ids": [m["member_id"]]}
        )
        d = ws.receive_json()

    assert d["type"] == "rejected"
    assert "已退出聊天室，不在线" in d["detail"]
    assert client.get(f"/rooms/{rid}/messages").json() == []


def test_leave_clears_pending_hand_and_floor(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.post(f"/rooms/{rid}/start")
    m = _join_member(client, rid, "林工")
    mc = client.as_member(m["member_id"], m["token"])
    # 成员举手 → 主持人放行（授予发言权）→ 成员退出：发言权与排队举手都该清掉
    mc.post(f"/rooms/{rid}/raise-hand/self", json={"intent": "补充"})
    hands = client.get(f"/rooms/{rid}/raise-hand").json()
    rid_hand = hands[0]["raise_id"]
    client.post(f"/rooms/{rid}/raise-hand/{rid_hand}/release")
    assert env.floor.holds(rid, m["member_id"])

    assert mc.post(f"/rooms/{rid}/leave").status_code == 200

    assert not env.floor.holds(rid, m["member_id"])
    assert all(h["member_id"] != m["member_id"] for h in client.get(f"/rooms/{rid}/raise-hand").json())
