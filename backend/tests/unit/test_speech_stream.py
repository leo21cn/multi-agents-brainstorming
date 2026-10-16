import pytest
from sqlmodel import Session

from app.domain.enums import SpeechPace
from app.domain.models import Agent
from app.models.provider import CONTENT, END, THOUGHT, StreamPiece
from app.services.speech_service import SpeechError


def _content(*texts):
    return [StreamPiece(CONTENT, t) for t in texts] + [StreamPiece(END, "", False)]


def _set_pace(env, agent_id, pace_value):
    with Session(env.engine) as s:
        agent = s.get(Agent, agent_id)
        agent.speech_pace = SpeechPace(pace_value)
        s.add(agent)
        s.commit()


class ChunkProvider:
    def __init__(self, pieces):
        self._pieces = pieces

    def stream_chat(self, model_name, messages, pace=None, **kw):
        yield from self._pieces


class ThoughtProvider:
    def stream_chat(self, model_name, messages, pace=None, **kw):
        yield StreamPiece(THOUGHT, "想一想")
        yield StreamPiece(THOUGHT, "二")
        yield from _content("结论")


class BoomStream:
    def stream_chat(self, model_name, messages, pace=None, **kw):
        yield StreamPiece(CONTENT, "开头")
        raise RuntimeError("timeout")


class BoomRetrieval:
    def retrieve(self, query, kb_ids):
        raise RuntimeError("embedding endpoint unreachable")


def test_flash_pace_yields_two_stages(env, seeded, make_room):
    """快闪关思考，无思考片段即不得出现「思考中」阶段。"""
    agent_id = seeded["agent"]["agent_id"]
    _set_pace(env, agent_id, "快闪")
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: ChunkProvider(_content("甲", "乙", "丙"))

    events = list(env.speech.generate_stream(room["room_id"], agent_id))
    kinds = [k for k, _ in events]

    assert kinds[0] == "stage" and kinds[-1] == "message"
    assert kinds.count("stage") == 2
    assert "thought" not in kinds
    assert "".join(v for k, v in events if k == "delta") == "甲乙丙"
    assert events[-1][1].content == "甲乙丙"
    assert events[-1][1].truncated is False
    assert len(env.memory.all_messages(room["room_id"])) == 1


def test_balanced_pace_yields_think_stage_and_thought_events(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: ThoughtProvider()

    events = list(env.speech.generate_stream(room["room_id"], agent_id))
    kinds = [k for k, _ in events]

    assert kinds.count("stage") == 3
    assert [v for k, v in events if k == "stage"] == ["检索知识库", "思考中", "生成中"]
    assert "".join(v for k, v in events if k == "thought") == "想一想二"
    assert kinds.index("thought") < kinds.index("delta")


def test_stream_stage_precedes_first_delta(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: ChunkProvider(_content("甲"))

    kinds = [k for k, _ in env.speech.generate_stream(room["room_id"], agent_id)]

    assert kinds.index("stage") < kinds.index("delta")


def test_stream_skips_empty_deltas(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: ChunkProvider(_content("甲", "", "乙"))

    deltas = [v for k, v in env.speech.generate_stream(room["room_id"], agent_id) if k == "delta"]

    assert deltas == ["甲", "乙"]


def test_stream_failure_persists_nothing(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: BoomStream()

    with pytest.raises(SpeechError):
        list(env.speech.generate_stream(room["room_id"], agent_id))

    assert env.memory.all_messages(room["room_id"]) == []


def test_stream_commit_matches_generate(env, seeded, make_room):
    """两条调用路径消费同一段增量，落库结果必须一致。"""
    agent_id = seeded["agent"]["agent_id"]
    env.speech.provider_factory = lambda a: ChunkProvider(_content("同一内容"))
    streamed = [
        v for k, v in env.speech.generate_stream(make_room(agent_id)["room_id"], agent_id) if k == "message"
    ][0]

    plain = env.speech.generate(make_room(agent_id)["room_id"], agent_id)

    assert streamed.content == plain.content
    assert streamed.truncated == plain.truncated
    assert streamed.source_type == plain.source_type
    assert streamed.cited_kb_ids == plain.cited_kb_ids
    assert streamed.no_kb_citation == plain.no_kb_citation


def test_stream_unknown_agent_raises(env, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    with pytest.raises(SpeechError):
        list(env.speech.generate_stream(room["room_id"], "agent_missing"))


def test_stream_retrieval_failure_is_speech_error(env, seeded, make_room):
    """检索抛出的底层异常须收束为 SpeechError，且发生在任何增量之前。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.retrieval = BoomRetrieval()

    events = []
    with pytest.raises(SpeechError):
        for item in env.speech.generate_stream(room["room_id"], agent_id):
            events.append(item)

    assert [k for k, _ in events] == ["stage"]
    assert env.memory.all_messages(room["room_id"]) == []


def test_generate_retrieval_failure_is_speech_error(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    env.speech.retrieval = BoomRetrieval()

    with pytest.raises(SpeechError):
        env.speech.generate(room["room_id"], agent_id)

    assert env.memory.all_messages(room["room_id"]) == []
