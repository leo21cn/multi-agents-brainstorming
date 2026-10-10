def test_ws_point_broadcasts_message(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "point", "agent_id": seeded["agent"]["agent_id"]})
        evt = ws.receive_json()
        assert evt["type"] in {"message", "speech_failed"}


def test_ws_raise_hand_event(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with client.websocket_connect(f"/rooms/{room['room_id']}/ws") as ws:
        ws.send_json({"type": "raise_hand", "agent_id": seeded["agent"]["agent_id"]})
        evt = ws.receive_json()
        assert evt["type"] == "raise_hand"
