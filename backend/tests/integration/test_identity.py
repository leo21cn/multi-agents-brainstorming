import pytest
from starlette.websockets import WebSocketDisconnect

from app.api import ws as ws_mod


def _join(client, rid, name):
    return client.post(f"/rooms/{rid}/join", json={"display_name": name}).json()


def _drain(ws, stop, limit=80):
    """收取事件直到 stop(events) 为真；limit 用于防止实现缺陷导致测试挂起。"""
    events = []
    for _ in range(limit):
        evt = ws.receive_json()
        events.append(evt)
        if stop(events):
            break
    return events


def test_ws_without_identity_never_joins_the_room(client, pair):
    """无身份的连接不得进入接收循环：否则它能收到全部房间事件。"""
    rid = pair["room"]["room_id"]

    with pytest.raises(WebSocketDisconnect):
        with client.raw.websocket_connect(f"/rooms/{rid}/ws"):
            pass

    assert ws_mod.manager._rooms.get(rid, []) == []


def test_ws_with_a_wrong_token_never_joins_the_room(client, pair):
    rid = pair["room"]["room_id"]

    with pytest.raises(WebSocketDisconnect):
        with client.raw.websocket_connect(
            f"/rooms/{rid}/ws?member_id={pair['host_id']}&token=deadbeef"
        ):
            pass

    assert ws_mod.manager._rooms.get(rid, []) == []


def test_a_member_identity_is_accepted_on_the_ws_handshake(client, pair):
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")
    as_guest = client.as_member(guest["member_id"], guest["token"])

    with as_guest.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "room_state"})
        evt = ws.receive_json()

    assert evt["type"] == "room_state"
    # 连接退出后须从广播名单摘掉，否则后续广播会朝一条死连接投递
    assert ws_mod.manager._rooms.get(rid, []) == []


def test_rest_messages_require_an_identity(client, pair):
    rid = pair["room"]["room_id"]

    r = client.raw.post(f"/rooms/{rid}/messages", json={"content": "我有一句"})

    assert r.status_code == 401
    assert r.json()["detail"] == "身份缺失或已失效，请重新进房"


def test_human_message_is_attributed_to_its_sender(client, pair):
    """谁发的就记成谁：实施前一律写成房间主持人，成员的话全被记到主持人头上。"""
    rid = pair["room"]["room_id"]
    guest = _join(client, rid, "林工")

    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "human_message", "content": "主持人开场"})
        _drain(ws, lambda ev: any(e["type"] == "message" for e in ev))

    msgs = client.get(f"/rooms/{rid}/messages").json()
    assert [m["source_id"] for m in msgs] == [pair["host_id"]]
    assert guest["member_id"] != pair["host_id"]
