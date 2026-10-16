from app.domain.enums import SourceType
from app.memory.context import assemble_context


def test_context_respects_budget_and_order():
    msgs = [{"role": "assistant", "content": "x" * 100} for _ in range(50)]
    ctx = assemble_context("命题", "摘要", msgs, "立场", [], budget=200)
    assert sum(len(m["content"]) for m in ctx) <= 200
    assert ctx[0]["content"].startswith("命题")


def test_focus_block_sits_after_proposition():
    out = assemble_context(
        proposition="命题甲",
        l2_summary="摘要乙",
        recent_messages=[{"role": "assistant", "content": "丙"}],
        stance="立场丁",
        kb_hits=[],
        budget=6000,
        focus="陈向你提问：戊",
    )

    assert out[0]["content"] == "命题：命题甲"
    assert out[1] == {"role": "system", "content": "当前焦点：陈向你提问：戊"}
    assert out[2]["content"] == "讨论摘要：摘要乙"


def test_no_focus_keeps_existing_order():
    out = assemble_context("命题甲", "摘要乙", [], None, [], 6000)

    assert [b["content"] for b in out] == ["命题：命题甲", "讨论摘要：摘要乙"]


def test_focus_survives_tight_budget():
    out = assemble_context(
        proposition="命题甲",
        l2_summary="摘" * 500,
        recent_messages=[{"role": "assistant", "content": "丙" * 500}],
        stance=None,
        kb_hits=[],
        budget=80,
        focus="陈向你提问：戊",
    )

    assert any(b["content"].startswith("当前焦点：") for b in out)
    assert not any(b["content"].startswith("讨论摘要：") for b in out)


def test_build_context_signs_recent_messages(env, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, room["host_member_id"], "问题甲")

    ctx = env.memory.build_context(
        "命题",
        rid,
        seeded["agent"]["agent_id"],
        name_of=lambda st, sid: "陈" if st == SourceType.HUMAN else "架构师",
    )

    signed = [b for b in ctx if b["content"].startswith("[")]
    assert signed == [{"role": "assistant", "content": "[陈·人类] 问题甲"}]


def test_build_context_without_name_of_keeps_bare_text(env, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    env.memory.append_message(rid, SourceType.HUMAN, room["host_member_id"], "问题甲")

    ctx = env.memory.build_context("命题", rid, seeded["agent"]["agent_id"])

    assert {"role": "assistant", "content": "问题甲"} in ctx
