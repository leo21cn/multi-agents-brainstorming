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


def _updated(ws):
    return _drain(ws, lambda ev: any(e["type"] == "raise_hand_update" for e in ev))


def test_a_member_raises_his_own_hand(client, pair):
    """成员与智能体同权：插话前先举手，放行权仍在主持人手里。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        ws.send_json({"type": "raise_self", "intent": "我想补一句排期"})
        _updated(ws)

    queued = client.get(f"/rooms/{rid}/raise-hand").json()
    assert [q["member_id"] for q in queued] == [guest["member_id"]]
    assert [q["agent_id"] for q in queued] == [None]
    assert [q["source"] for q in queued] == ["member"]
    assert [q["intent"] for q in queued] == ["我想补一句排期"]


def test_a_member_cannot_raise_on_someone_elses_behalf(client, pair):
    """主体取自连接身份，故载荷里塞别人也不生效。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        ws.send_json(
            {"type": "raise_self", "intent": "替他举手", "agent_id": pair["first"],
             "member_id": pair["host_id"]}
        )
        _updated(ws)

    queued = client.get(f"/rooms/{rid}/raise-hand").json()
    assert [q["member_id"] for q in queued] == [guest["member_id"]]
    assert [q["agent_id"] for q in queued] == [None]


def test_raising_twice_rewrites_the_intent_and_keeps_one_hand(client, pair):
    """上下文变了、想说的话也变了：改写而不是再排一条。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        ws.send_json({"type": "raise_self", "intent": "旧意图"})
        _updated(ws)
        first = client.get(f"/rooms/{rid}/raise-hand").json()
        ws.send_json({"type": "raise_self", "intent": "新意图"})
        _updated(ws)

    queued = client.get(f"/rooms/{rid}/raise-hand").json()
    assert [q["raise_id"] for q in queued] == [q["raise_id"] for q in first]
    assert [q["intent"] for q in queued] == ["新意图"]


def test_a_member_withdraws_his_own_hand(client, pair):
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])

    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "raise_self", "intent": "想补一句"})
        _updated(ws)
        raise_id = client.get(f"/rooms/{rid}/raise-hand").json()[0]["raise_id"]
        ws.send_json({"type": "withdraw_hand", "raise_id": raise_id})
        _updated(ws)

    assert client.get(f"/rooms/{rid}/raise-hand").json() == []


def test_a_member_cannot_withdraw_another_subjects_hand(client, pair):
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    agent_hand = client.post(
        f"/rooms/{rid}/raise-hand", json={"agent_id": pair["first"], "intent": "智能体想说"}
    ).json()

    with client.as_member(guest["member_id"], guest["token"]).websocket_connect(
        f"/rooms/{rid}/ws"
    ) as ws:
        ws.send_json({"type": "withdraw_hand", "raise_id": agent_hand["raise_id"]})
        _updated(ws)

    assert [q["raise_id"] for q in client.get(f"/rooms/{rid}/raise-hand").json()] == [
        agent_hand["raise_id"]
    ]


def test_the_host_can_withdraw_any_hand(client, pair):
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])
    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "raise_self", "intent": "想补一句"})
    raise_id = client.get(f"/rooms/{rid}/raise-hand").json()[0]["raise_id"]

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "withdraw_hand", "raise_id": raise_id})
        _updated(ws)

    assert client.get(f"/rooms/{rid}/raise-hand").json() == []


def test_rest_self_raise_and_withdraw_mirror_the_ws_path(client, pair):
    """降级通道的举手与撤回同样广播：主持人的实时通道不该停在旧队列上。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])

    with client.websocket_connect(f"/rooms/{rid}/ws") as host_ws:
        r = as_guest.post(f"/rooms/{rid}/raise-hand/self", json={"intent": "降级通道也想说话"})
        assert r.status_code == 201
        raise_id = r.json()["raise_id"]
        # room_state 一定有回执，故用它收束收取，不广播时用例是失败而不是挂起
        host_ws.send_json({"type": "room_state"})
        events = _drain(host_ws, lambda ev: any(e["type"] == "room_state" for e in ev))
        assert any(e["type"] == "raise_hand_update" for e in events), [e["type"] for e in events]

        assert as_guest.post(f"/rooms/{rid}/raise-hand/{raise_id}/withdraw").status_code == 200
        host_ws.send_json({"type": "room_state"})
        events = _drain(host_ws, lambda ev: any(e["type"] == "room_state" for e in ev))
        assert any(e["type"] == "raise_hand_update" for e in events), [e["type"] for e in events]

    assert client.get(f"/rooms/{rid}/raise-hand").json() == []


def test_rest_sim_raise_and_dismiss_reach_live_channels(client, pair):
    """主持人代填与驳回同样可能走降级通道，成员侧的队列要跟得上。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])

    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        r = client.post(
            f"/rooms/{rid}/raise-hand", json={"agent_id": pair["first"], "intent": "代填一条"}
        )
        assert r.status_code == 201
        raise_id = r.json()["raise_id"]
        ws.send_json({"type": "room_state"})
        events = _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))
        assert any(e["type"] == "raise_hand_update" for e in events), [e["type"] for e in events]

        assert client.post(f"/rooms/{rid}/raise-hand/{raise_id}/dismiss").status_code == 200
        ws.send_json({"type": "room_state"})
        events = _drain(ws, lambda ev: any(e["type"] == "room_state" for e in ev))
        assert any(e["type"] == "raise_hand_update" for e in events), [e["type"] for e in events]

    assert client.get(f"/rooms/{rid}/raise-hand").json() == []


def test_a_host_cannot_self_raise(client, pair):
    """主持人有话直接说，不必举手；这条入口只对普通成员开放。"""
    rid = pair["room"]["room_id"]

    r = client.post(f"/rooms/{rid}/raise-hand/self", json={"intent": "主持人举手"})

    assert r.status_code == 403
