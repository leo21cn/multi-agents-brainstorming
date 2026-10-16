from app.models.provider import CONTENT, END, StreamPiece
from app.domain.enums import SourceType


def test_context_signs_each_human_member_by_name(client, env, seeded, make_room):
    """智能体要能分辨是哪个人说的话，否则两个人的话在它眼里是同一个人。"""
    rid = make_room(seeded["agent"]["agent_id"])["room_id"]
    client.post(f"/rooms/{rid}/start")
    guest = client.post(f"/rooms/{rid}/join", json={"display_name": "林工"}).json()
    host_id = client.get(f"/rooms/{rid}").json()["host_member_id"]
    env.memory.append_message(rid, SourceType.HUMAN, host_id, "主持人问一句")
    env.memory.append_message(rid, SourceType.HUMAN, guest["member_id"], "成员答一句")
    seen = []

    class Capture:
        def stream_chat(self, model_name, messages, pace=None, **kw):
            seen.extend(messages)
            yield StreamPiece(CONTENT, "好")
            yield StreamPiece(END, "", False)

    env.speech.provider_factory = lambda agent: Capture()
    env.speech.generate(rid, seeded["agent"]["agent_id"])

    # 只取人类发言的署名行：检索块与系统段也可能以 "[" 起头，按 "·人类] " 精确筛
    signed = [m["content"] for m in seen if "·人类] " in m["content"]]
    assert signed == ["[陈·人类] 主持人问一句", "[林工·人类] 成员答一句"]


def test_legacy_host_literal_still_signs_as_the_host(env, seeded, make_room):
    """字面值 "host" 是实施前落库的历史消息，迁移不改写它，署名继续按主持人回落。"""
    rid = make_room(seeded["agent"]["agent_id"])["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, "host", "旧消息")
    seen = []

    class Capture:
        def stream_chat(self, model_name, messages, pace=None, **kw):
            seen.extend(messages)
            yield StreamPiece(CONTENT, "好")
            yield StreamPiece(END, "", False)

    env.speech.provider_factory = lambda agent: Capture()
    env.speech.generate(rid, seeded["agent"]["agent_id"])

    # 只取人类发言的署名行：检索块与系统段也可能以 "[" 起头，按 "·人类] " 精确筛
    signed = [m["content"] for m in seen if "·人类] " in m["content"]]
    assert signed == ["[陈·人类] 旧消息"]
