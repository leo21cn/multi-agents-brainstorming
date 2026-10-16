def _join(client, rid, name):
    return client.post(f"/rooms/{rid}/join", json={"display_name": name}).json()


def _drain(ws, stop, limit=80):
    events = []
    for _ in range(limit):
        evt = ws.receive_json()
        events.append(evt)
        if stop(events):
            break
    return events


def _floor(ws):
    return _drain(ws, lambda ev: any(e["type"] == "floor_update" for e in ev))


def test_the_host_directing_a_member_grants_him_the_floor(client, pair):
    """主持人 @ 一个人类成员，等同放行：该成员获得一次发言权。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])

    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        client.post(
            f"/rooms/{rid}/messages",
            json={"content": "@林工 你怎么看", "addressee_member_ids": [guest["member_id"]]},
        )
        events = _floor(ws)

    update = [e for e in events if e["type"] == "floor_update"][-1]
    assert update["member_ids"] == [guest["member_id"]]
    assert update["granted"] == [{"member_id": guest["member_id"], "reason": "addressed"}]

    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "我回答"})
        _drain(ws, lambda ev: any(e["type"] == "message" for e in ev))

    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert [m["content"] for m in msgs] == ["@林工 你怎么看", "我回答"]
    assert [m["source_id"] for m in msgs] == [pair["host_id"], guest["member_id"]]
    assert msgs[0]["addressee_member_ids"] == [guest["member_id"]]


def test_a_member_directing_another_member_grants_nothing(client, env, pair):
    """发言触发权全部经主持人；成员之间不能私相授受。"""
    rid = pair["room"]["room_id"]
    first = _join(client, rid, "林工")
    second = _join(client, rid, "赵工")
    env.floor.grant(rid, first["member_id"])
    as_first = client.as_member(first["member_id"], first["token"])

    with as_first.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json(
            {
                "type": "human_message",
                "content": "@赵工 你说",
                "addressee_member_ids": [second["member_id"]],
            }
        )
        _drain(
            ws,
            lambda ev: any(
                e["type"] == "message" and e["message"]["source_type"] == "人类" for e in ev
            ),
        )

    # 标注照旧落库，发言权一个也没发出去
    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert msgs[0]["addressee_member_ids"] == [second["member_id"]]
    assert client.get(f"/rooms/{rid}").json()["floor_member_ids"] == []


def test_a_member_directing_an_agent_does_not_make_it_speak(client, env, pair):
    """成员的 @ 只作标注：触发智能体应答要经主持人放行，实时通道上也不例外。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    env.floor.grant(rid, guest["member_id"])

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        ws.send_json(
            {
                "type": "human_message",
                "content": "@架构师 你怎么看",
                "addressee_agent_ids": [pair["first"]],
            }
        )
        _drain(ws, lambda ev: any(e["type"] == "message" for e in ev))

    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert [m["source_type"] for m in msgs] == ["人类"]
    assert msgs[0]["addressee_agent_ids"] == [pair["first"]]


def test_directing_a_member_supersedes_his_queued_hand(client, env, pair):
    """主持人已经直接问他了，那条举手就过期了。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    env.queue.enqueue(rid, None, "想补一句", "member", member_id=guest["member_id"])

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        client.post(
            f"/rooms/{rid}/messages",
            json={"content": "@林工 你先说", "addressee_member_ids": [guest["member_id"]]},
        )
        events = _drain(
            ws,
            lambda ev: any(e["type"] == "floor_update" for e in ev)
            and any(e["type"] == "raise_hand_update" for e in ev),
        )

    assert client.get(f"/rooms/{rid}/raise-hand").json() == []
    assert [e["type"] for e in events].count("raise_hand_update") >= 1


def test_an_addressee_outside_the_room_is_refused(client, pair):
    rid = pair["room"]["room_id"]

    r = client.post(
        f"/rooms/{rid}/messages",
        json={"content": "喂", "addressee_member_ids": ["mem_nope"]},
    )

    assert r.status_code == 422
    assert r.json()["detail"] == "受话者不在房间内"


def test_the_host_cannot_grant_himself_a_floor_by_self_addressing(client, pair):
    """把自己列进受话者不产生发言权条目：主持人本来就不受发言权约束。"""
    rid = pair["room"]["room_id"]

    client.post(
        f"/rooms/{rid}/messages",
        json={"content": "自问自答", "addressee_member_ids": [pair["host_id"]]},
    )

    assert client.get(f"/rooms/{rid}").json()["floor_member_ids"] == []


def test_directing_an_agent_and_a_member_in_one_message(client, env, pair):
    """两列并存：智能体列决定串行应答，成员列只决定发言权。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json(
            {
                "type": "human_message",
                "content": "@架构师 @林工 各说一句",
                "addressee_agent_ids": [pair["first"]],
                "addressee_member_ids": [guest["member_id"]],
            }
        )
        events = _drain(
            ws,
            lambda ev: any(
                e["type"] == "message" and e["message"]["source_type"] == "智能体" for e in ev
            ),
        )

    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert msgs[0]["addressee_agent_ids"] == [pair["first"]]
    assert msgs[0]["addressee_member_ids"] == [guest["member_id"]]
    assert client.get(f"/rooms/{rid}").json()["floor_member_ids"] == [guest["member_id"]]
    assert [e["type"] for e in events].count("floor_update") >= 1
