def test_export_count_matches_messages(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(
        f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]}
    )
    client.post(f"/rooms/{room['room_id']}/messages", json={"content": "人类补充"})
    exp = client.post(f"/rooms/{room['room_id']}/export").json()
    msgs = client.get(f"/rooms/{room['room_id']}/messages").json()
    assert exp["entry_count"] == len(msgs)
    assert exp["entry_count"] >= 2


def test_raise_hand_release_triggers_speech(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    agent_id = seeded["agent"]["agent_id"]
    rh = client.post(
        f"/rooms/{room['room_id']}/raise-hand", json={"agent_id": agent_id, "intent": "补充成本"}
    ).json()
    assert [i["agent_id"] for i in client.get(f"/rooms/{room['room_id']}/raise-hand").json()] == [
        agent_id
    ]
    r = client.post(f"/rooms/{room['room_id']}/raise-hand/{rh['raise_id']}/release")
    assert r.status_code == 201
    assert client.get(f"/rooms/{room['room_id']}/raise-hand").json() == []
