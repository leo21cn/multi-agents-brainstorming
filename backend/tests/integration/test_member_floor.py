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


def test_member_cannot_send_without_the_floor(client, pair):
    """A-1：非主持人成员与智能体同权，须被放行才可插话。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        ws.send_json({"type": "human_message", "content": "我插一句"})
        events = _drain(ws, lambda ev: any(e["type"] == "rejected" for e in ev))

    assert [e["detail"] for e in events if e["type"] == "rejected"] == [
        "尚未获得发言权：举手并经主持人放行，或由主持人 @ 你"
    ]
    assert client.get(f"/rooms/{rid}/messages").json() == []


def test_releasing_a_member_hand_grants_the_floor_once(client, env, pair):
    """放行人类举手不触发生成，而是授予一次发言权；发出一条即消耗。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])
    hand = env.queue.enqueue(rid, None, "我想补一句", "member", member_id=guest["member_id"])

    with client.websocket_connect(f"/rooms/{rid}/ws") as host_ws:
        with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
            host_ws.send_json({"type": "release", "raise_id": hand.raise_id})
            events = _drain(
                ws, lambda ev: any(e["type"] == "floor_update" for e in ev)
            )
            granted = [e for e in events if e["type"] == "floor_update"][-1]
            assert granted["member_ids"] == [guest["member_id"]]
            assert granted["granted"] == [
                {"member_id": guest["member_id"], "reason": "released"}
            ]

            ws.send_json({"type": "human_message", "content": "我补一句"})
            _drain(
                ws,
                lambda ev: any(
                    e["type"] == "message" and e["message"]["source_type"] == "人类" for e in ev
                ),
            )
            ws.send_json({"type": "human_message", "content": "我再说一句"})
            after = _drain(ws, lambda ev: any(e["type"] == "rejected" for e in ev))

    assert env.floor.holders(rid) == []
    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert [m["content"] for m in msgs] == ["我补一句"]
    assert [m["source_id"] for m in msgs] == [guest["member_id"]]
    assert [e["detail"] for e in after if e["type"] == "rejected"] == [
        "尚未获得发言权：举手并经主持人放行，或由主持人 @ 你"
    ]


def test_host_is_never_blocked_by_the_floor(client, pair):
    """主持人掌握节奏，发言不必先获授权。"""
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "开场"})
        events = _drain(
            ws, lambda ev: any(e["type"] == "message" for e in ev)
        )

    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert [m["content"] for m in msgs] == ["开场"]
    assert [m["source_id"] for m in msgs] == [pair["host_id"]]
    assert not [e for e in events if e["type"] == "rejected"]


def test_member_cannot_run_the_host_actions(client, pair):
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])

    assert as_guest.post(f"/rooms/{rid}/end").status_code == 403
    assert as_guest.post(f"/rooms/{rid}/start").status_code == 403
    assert as_guest.post(f"/rooms/{rid}/conclusion").status_code == 403
    assert as_guest.post(f"/rooms/{rid}/export").status_code == 403
    assert as_guest.post(
        f"/rooms/{rid}/host", json={"member_id": guest["member_id"]}
    ).status_code == 403
    assert as_guest.post(
        f"/rooms/{rid}/raise-hand", json={"agent_id": pair["first"], "intent": "x"}
    ).status_code == 403


def test_member_cannot_drive_the_room_over_the_ws(client, pair):
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])

    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": pair["first"]})
        events = _drain(ws, lambda ev: any(e["type"] == "rejected" for e in ev))

    assert [e["detail"] for e in events if e["type"] == "rejected"] == [
        "该操作只有主持人可执行"
    ]


def test_room_payload_carries_the_floor(client, env, pair):
    """刷新与重连后发言权不丢：集合随房间一起下发。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    env.floor.grant(rid, guest["member_id"])

    body = client.get(f"/rooms/{rid}").json()

    assert body["floor_member_ids"] == [guest["member_id"]]


def test_joining_notifies_everyone_in_the_room(client, pair):
    """主持人的「已加入名单」要实时刷新，否则他得手动刷页面才知道谁进来了。"""
    rid = pair["room"]["room_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        _join(client, rid, "林工")
        events = _drain(ws, lambda ev: any(e["type"] == "member_update" for e in ev))

    assert [e["type"] for e in events if e["type"] == "member_update"] == ["member_update"]


def test_ending_the_room_clears_the_floor_and_notifies(client, env, pair):
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    env.floor.grant(rid, guest["member_id"])

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        client.post(f"/rooms/{rid}/end")
        events = _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))

    assert env.floor.holders(rid) == []
    state = [e for e in events if e["type"] == "room_state"][-1]
    assert state["room"]["status"] == "已结束"


def test_rest_release_reaches_the_members_live_channel(client, env, pair):
    """主持人的实时通道断了不等于成员的也断了：降级放行同样要广播发言权。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])
    hand = env.queue.enqueue(rid, None, "我想补一句", "member", member_id=guest["member_id"])

    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        assert client.post(f"/rooms/{rid}/raise-hand/{hand.raise_id}/release").status_code == 201
        # room_state 一定有回执，故用它收束收取，不广播时用例是失败而不是挂起
        ws.send_json({"type": "room_state"})
        events = _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))

    granted = [e for e in events if e["type"] == "floor_update"]
    assert granted, [e["type"] for e in events]
    assert granted[-1]["member_ids"] == [guest["member_id"]]
    assert granted[-1]["granted"] == [{"member_id": guest["member_id"], "reason": "released"}]
    assert any(e["type"] == "raise_hand_update" for e in events)


def test_rest_human_message_reaches_live_channels(client, env, pair):
    """成员走降级通道发言时，主持人的实时通道同样要收到这条消息。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])
    env.floor.grant(rid, guest["member_id"])

    with client.websocket_connect(f"/rooms/{rid}/ws") as host_ws:
        r = as_guest.post(f"/rooms/{rid}/messages", json={"content": "我补一句"})
        assert r.status_code == 201
        host_ws.send_json({"type": "room_state"})
        events = _drain(host_ws, lambda ev: any(e["type"] == "room_state" for e in ev))

    msgs = [e for e in events if e["type"] == "message"]
    assert msgs, [e["type"] for e in events]
    assert msgs[-1]["message"]["content"] == "我补一句"
    assert msgs[-1]["message"]["source_id"] == guest["member_id"]
