SEVEN = {
    "命题",
    "关键观点",
    "分歧点",
    "初步结论／建议",
    "行动项与负责人",
    "待定项",
    "风险",
}


def test_conclusion_seven_sections_and_annotations(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(
        f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]}
    )
    client.post(f"/rooms/{room['room_id']}/messages", json={"content": "补充一个合规约束。"})
    r = client.post(f"/rooms/{room['room_id']}/conclusion")
    assert r.status_code == 201
    doc = r.json()
    assert set(doc["sections"].keys()) == SEVEN
    assert len(doc["source_annotations"]) >= 1
    for ann in doc["source_annotations"]:
        assert ann["agent"] and ann["kbs"]


def test_conclusion_empty_memory_rejected(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    assert client.post(f"/rooms/{room['room_id']}/conclusion").status_code == 422
