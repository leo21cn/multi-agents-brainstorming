from app.domain.enums import SourceType
from app.models.provider import CONTENT, END, StreamPiece


def test_human_message_stores_addressee(client, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)

    r = client.post(
        f"/rooms/{room['room_id']}/messages",
        json={"content": "@架构师 这个方案的回滚成本怎么算？", "addressee_agent_ids": [agent_id]},
    )

    assert r.status_code == 201
    body = r.json()
    assert body["addressee_agent_ids"] == [agent_id]
    assert body["source_type"] == "人类"
    assert body["content"].startswith("@架构师")


def test_human_message_without_addressee_is_broadcast(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])

    r = client.post(f"/rooms/{room['room_id']}/messages", json={"content": "各位"})

    assert r.json()["addressee_agent_ids"] == []


def test_addressee_must_be_in_roster(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])

    r = client.post(
        f"/rooms/{room['room_id']}/messages",
        json={"content": "@外人 说一句", "addressee_agent_ids": ["agent_not_in_room"]},
    )

    assert r.status_code == 422
    assert r.json()["detail"] == "受话者不在房间阵容内"
    assert client.get(f"/rooms/{room['room_id']}/messages").json() == []


def test_any_addressee_outside_roster_rejects_the_whole_message(client, seeded, make_room):
    """半接受会让落库消息的收件人与原文里的 @ 名字对不上，故整条拒绝。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)

    r = client.post(
        f"/rooms/{room['room_id']}/messages",
        json={
            "content": "@架构师 @外人 说一句",
            "addressee_agent_ids": [agent_id, "agent_not_in_room"],
        },
    )

    assert r.status_code == 422
    assert r.json()["detail"] == "受话者不在房间阵容内"
    assert client.get(f"/rooms/{room['room_id']}/messages").json() == []


def test_duplicate_addressees_collapse_keeping_order(client, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)

    r = client.post(
        f"/rooms/{room['room_id']}/messages",
        json={
            "content": "@架构师 @架构师 说一句",
            "addressee_agent_ids": [agent_id, agent_id],
        },
    )

    assert r.json()["addressee_agent_ids"] == [agent_id]


def test_human_message_source_is_host_member(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])

    r = client.post(f"/rooms/{room['room_id']}/messages", json={"content": "记一下"})

    assert r.json()["source_id"] == room["host_member_id"]
    assert r.json()["source_id"] != "host"


def test_messages_rejected_in_ended_room(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(f"/rooms/{room['room_id']}/end")

    r = client.post(f"/rooms/{room['room_id']}/messages", json={"content": "还有一句"})

    assert r.status_code == 422
    assert r.json()["detail"] == "房间已结束，不再新增发言"
    assert client.get(f"/rooms/{room['room_id']}/messages").json() == []


class CapturingProvider:
    """记下收到的上下文，供断言提示词组装。"""

    def __init__(self):
        self.messages = None

    def stream_chat(self, model_name, messages, pace=None, **kw):
        self.messages = messages
        yield StreamPiece(CONTENT, "答")
        yield StreamPiece(END, "", False)


def test_directed_question_reaches_prompt_as_focus(env, seeded, make_room):
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
    cap = CapturingProvider()
    env.speech.provider_factory = lambda a: cap

    env.speech.generate(rid, agent_id)

    focus = [m for m in cap.messages if m["content"].startswith("当前焦点：")]
    assert focus == [{"role": "system", "content": "当前焦点：陈向你提问：回滚成本怎么算？"}]


def _second_agent(client, seeded, name="项目经理"):
    return client.post(
        "/agents",
        json={
            "name": name,
            "model_connection_id": seeded["conn"]["conn_id"],
            "model_name": "qwen-plus",
            "kb_ids": [seeded["kb"]["kb_id"]],
            "persona": "资深项目经理",
            "expertise": "排期与范围",
        },
    ).json()["agent_id"]


def test_second_addressee_focus_is_anchored_by_message_id(client, env, seeded, make_room):
    """多受话者依次应答：轮到第二个人时提问已不是最后一条，焦点须靠消息标识取回。"""
    agent_id = seeded["agent"]["agent_id"]
    second = _second_agent(client, seeded)
    room = make_room(agent_id)
    rid = room["room_id"]
    ask = env.memory.append_message(
        rid,
        SourceType.HUMAN,
        room["host_member_id"],
        "回滚成本怎么算？",
        addressee_agent_ids=[agent_id, second],
    )
    env.memory.append_message(rid, SourceType.AGENT, agent_id, "按批次算。")
    cap = CapturingProvider()
    env.speech.provider_factory = lambda a: cap

    env.speech.generate(rid, second, directed_from=ask.message_id)

    focus = [m for m in cap.messages if m["content"].startswith("当前焦点：")]
    assert focus == [{"role": "system", "content": "当前焦点：陈向你提问：回滚成本怎么算？"}]


def test_second_addressee_without_anchor_adds_no_focus(client, env, seeded, make_room):
    """无锚点时启发式仍只看最后一条：第二个受话者不会被塞进一个已经过时的焦点。"""
    agent_id = seeded["agent"]["agent_id"]
    second = _second_agent(client, seeded)
    room = make_room(agent_id)
    rid = room["room_id"]
    env.memory.append_message(
        rid,
        SourceType.HUMAN,
        room["host_member_id"],
        "回滚成本怎么算？",
        addressee_agent_ids=[agent_id, second],
    )
    env.memory.append_message(rid, SourceType.AGENT, agent_id, "按批次算。")
    cap = CapturingProvider()
    env.speech.provider_factory = lambda a: cap

    env.speech.generate(rid, second)

    assert not [m for m in cap.messages if m["content"].startswith("当前焦点：")]


def test_directed_from_pointing_at_agent_message_adds_no_focus(env, seeded, make_room):
    """锚点指向的不是人类提问时不产生焦点：锚点只用于定位待答提问，不用于改写来源。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    reply = env.memory.append_message(rid, SourceType.AGENT, agent_id, "先答一句。")
    cap = CapturingProvider()
    env.speech.provider_factory = lambda a: cap

    env.speech.generate(rid, agent_id, directed_from=reply.message_id)

    assert not [m for m in cap.messages if m["content"].startswith("当前焦点：")]


def test_undirected_last_message_adds_no_focus(env, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, room["host_member_id"], "各位")
    cap = CapturingProvider()
    env.speech.provider_factory = lambda a: cap

    env.speech.generate(rid, seeded["agent"]["agent_id"])

    assert not [m for m in cap.messages if m["content"].startswith("当前焦点：")]


def test_question_to_other_agent_adds_no_focus(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    env.memory.append_message(
        rid,
        SourceType.HUMAN,
        room["host_member_id"],
        "问你一句",
        addressee_agent_ids=["agent_someone_else"],
    )
    cap = CapturingProvider()
    env.speech.provider_factory = lambda a: cap

    env.speech.generate(rid, agent_id)

    assert not [m for m in cap.messages if m["content"].startswith("当前焦点：")]


def test_prompt_context_carries_speaker_names(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    rid = room["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, room["host_member_id"], "问题甲")
    cap = CapturingProvider()
    env.speech.provider_factory = lambda a: cap

    env.speech.generate(rid, agent_id)

    assert any(m["content"] == "[陈·人类] 问题甲" for m in cap.messages)
