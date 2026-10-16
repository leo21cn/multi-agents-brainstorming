from app.domain.enums import SourceType


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


def test_raise_hand_release_uses_intent(client, seeded, make_room, env):
    room = make_room(seeded["agent"]["agent_id"])
    seen = {}
    original = env.speech.generate

    def spy(room_id, agent_id, intent=None):
        seen["intent"] = intent
        return original(room_id, agent_id, intent)

    env.speech.generate = spy
    try:
        rh = client.post(
            f"/rooms/{room['room_id']}/raise-hand",
            json={"agent_id": seeded["agent"]["agent_id"], "intent": "补充成本"},
        ).json()
        client.post(f"/rooms/{room['room_id']}/raise-hand/{rh['raise_id']}/release")
    finally:
        env.speech.generate = original

    assert seen["intent"] == "补充成本"


def test_export_shows_display_names_and_addressee(client, env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    env.memory.append_message(
        rid,
        SourceType.HUMAN,
        room["host_member_id"],
        "回滚成本怎么算？",
        addressee_agent_ids=[agent_id],
    )
    env.memory.append_message(rid, SourceType.AGENT, agent_id, "按批次算。")

    r = client.post(f"/rooms/{rid}/export")

    assert r.status_code == 201
    assert r.json()["entry_count"] == 2
    text = client.get(f"/rooms/{rid}/export/{r.json()['export_id']}/download").text
    lines = text.splitlines()
    assert len(lines) == 2
    assert "人类·陈 → 架构师 回滚成本怎么算？" in lines[0]
    assert "智能体·架构师 按批次算。" in lines[1]
    assert "host" not in text
    assert agent_id not in text


def test_export_lists_every_addressee(client, env, seeded, make_room):
    """一条消息 @ 多个智能体时，导出须逐个列出，否则记录看不出问题问给了谁。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    env.memory.append_message(
        rid,
        SourceType.HUMAN,
        room["host_member_id"],
        "两位都说说。",
        addressee_agent_ids=[agent_id, "agent_gone"],
    )

    r = client.post(f"/rooms/{rid}/export")

    text = client.get(f"/rooms/{rid}/export/{r.json()['export_id']}/download").text
    assert "人类·陈 → 架构师、agent_gone 两位都说说。" in text


def test_export_falls_back_when_names_missing(client, env, seeded, make_room):
    """来源标识对不上任何成员或智能体时，退回通称与原始标识，不得抛错。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, "mem_unknown", "旁听一句")
    env.memory.append_message(rid, SourceType.AGENT, "agent_gone", "已删除智能体的旧发言")

    r = client.post(f"/rooms/{rid}/export")

    assert r.json()["entry_count"] == 2
    text = client.get(f"/rooms/{rid}/export/{r.json()['export_id']}/download").text
    assert "人类·人类成员 旁听一句" in text
    assert "智能体·agent_gone 已删除智能体的旧发言" in text


def test_export_marks_truncated_lines(client, env, seeded, make_room):
    """已落库的残篇须在导出中标注，否则导出成为静默失真的记录。"""
    agent_id = seeded["agent"]["agent_id"]
    rid = make_room(agent_id)["room_id"]
    env.memory.append_message(rid, SourceType.AGENT, agent_id, "先给结论：应按句界收尾。", truncated=True)
    env.memory.append_message(rid, SourceType.AGENT, agent_id, "完整一句。")

    r = client.post(f"/rooms/{rid}/export")

    assert r.json()["entry_count"] == 2
    text = client.get(f"/rooms/{rid}/export/{r.json()['export_id']}/download").text
    lines = text.splitlines()
    assert lines[0].endswith("先给结论：应按句界收尾。（篇幅所限，未展开）")
    assert not lines[1].endswith("（篇幅所限，未展开）")


def test_export_names_every_human_member(client, env, seeded, make_room):
    """导出按显示名逐行标注来源；只解析主持人一个会让其余人类一律回落通称。"""
    rid = make_room(seeded["agent"]["agent_id"])["room_id"]
    client.post(f"/rooms/{rid}/start")
    host_id = client.get(f"/rooms/{rid}").json()["host_member_id"]
    guest = client.post(f"/rooms/{rid}/join", json={"display_name": "林工"}).json()
    env.memory.append_message(rid, SourceType.HUMAN, guest["member_id"], "成员答一句")

    exp = client.post(f"/rooms/{rid}/export").json()
    text = client.get(f"/rooms/{rid}/export/{exp['export_id']}/download").text

    assert "人类·林工 成员答一句" in text
    assert "人类成员 成员答一句" not in text

    env.memory.append_message(
        rid, SourceType.HUMAN, host_id, "@林工 你怎么看", addressee_member_ids=[guest["member_id"]]
    )
    exp2 = client.post(f"/rooms/{rid}/export").json()
    text2 = client.get(f"/rooms/{rid}/export/{exp2['export_id']}/download").text
    assert "人类·陈 → 林工 @林工 你怎么看" in text2
