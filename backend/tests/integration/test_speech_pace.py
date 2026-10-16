"""发言档位贯通：提示词注入、档位解析下发、思考与截断的落库口径。"""

from sqlmodel import Session

from app.domain.enums import SpeechPace
from app.domain.models import Agent, Message
from app.models.provider import CONTENT, END, NOTICE, THOUGHT, StreamPiece
from app.models.thinking import resolve
from app.services.speech_service import build_system_prompt


class RecordingProvider:
    """记录收到的档位，并按脚本产出片段。"""

    def __init__(self, pieces=None):
        self._pieces = pieces if pieces is not None else [
            StreamPiece(CONTENT, "对外观点"),
            StreamPiece(END, "", False),
        ]
        self.seen_pace = "unset"

    def stream_chat(self, model_name, messages, pace=None, **kw):
        self.seen_pace = pace
        yield from self._pieces


def _set_pace(env, agent_id, pace_value):
    with Session(env.engine) as s:
        agent = s.get(Agent, agent_id)
        agent.speech_pace = SpeechPace(pace_value)
        s.add(agent)
        s.commit()


def test_prompt_injects_char_cap_after_style():
    """档位要求段排末位：长度与结构一律由它管，风格只决定视角与语气。"""
    prompt = build_system_prompt("架构师", "关注系统边界", "分层与解耦", "一句话点出根因", 600)

    assert prompt.startswith("你是架构师。")
    assert "600 字以内" in prompt
    assert "只给观点，不写文章" in prompt
    assert "不做调和式总结" in prompt
    assert prompt.index("发言风格") < prompt.index("本次发言要求")


def test_prompt_first_sentence_stays_name_only():
    """离线提供方以句号切分取智能体名，首句形态不得变。"""
    assert build_system_prompt("批判者", "p", "e", None, 240).split("。")[0] == "你是批判者"


def test_flash_pace_reaches_the_provider(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    _set_pace(env, agent_id, "快闪")
    room = make_room(agent_id)
    prov = RecordingProvider()
    env.speech.provider_factory = lambda a: prov

    list(env.speech.generate_stream(room["room_id"], agent_id))

    assert prov.seen_pace == resolve(SpeechPace.FLASH, "qwen-max")
    assert prov.seen_pace.thinking_enabled is False
    assert prov.seen_pace.max_chars == 240
    assert prov.seen_pace.ceiling_chars == 360


def test_truncated_end_is_persisted(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    _set_pace(env, agent_id, "快闪")
    room = make_room(agent_id)
    pieces = [StreamPiece(CONTENT, "甲。" * 60), StreamPiece(END, "", True)]
    env.speech.provider_factory = lambda a: RecordingProvider(pieces)

    events = list(env.speech.generate_stream(room["room_id"], agent_id))

    assert events[-1][1].truncated is True
    with Session(env.engine) as s:
        row = s.get(Message, events[-1][1].message_id)
    assert row.truncated is True


def test_notice_piece_becomes_a_visible_stage(env, seeded, make_room):
    """端点拒绝思考参数后的降级必须可见，不静默。"""
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    pieces = [
        StreamPiece(NOTICE, "该模型不接受思考参数，已按关思考生成"),
        StreamPiece(CONTENT, "正文"),
        StreamPiece(END, "", False),
    ]
    env.speech.provider_factory = lambda a: RecordingProvider(pieces)

    events = list(env.speech.generate_stream(room["room_id"], agent_id))
    stages = [v for k, v in events if k == "stage"]

    assert "该模型不接受思考参数，已按关思考生成" in stages
    assert stages[-1] == "生成中"


def test_thought_is_neither_persisted_nor_fed_back(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    room = make_room(agent_id)
    pieces = [
        StreamPiece(THOUGHT, "内部推理"),
        StreamPiece(CONTENT, "对外观点"),
        StreamPiece(END, "", False),
    ]
    env.speech.provider_factory = lambda a: RecordingProvider(pieces)

    events = list(env.speech.generate_stream(room["room_id"], agent_id))

    assert events[-1][1].content == "对外观点"
    assert env.memory.all_messages(room["room_id"])[0].content == "对外观点"
    context = "".join(
        b["content"] for b in env.memory.build_context("命题", room["room_id"], agent_id)
    )
    assert "内部推理" not in context


def test_generate_takes_truncation_from_the_end_piece(env, seeded, make_room):
    """一次性路径的截断标记只来自 END 片段：正文长度不再与截断等价。"""
    agent_id = seeded["agent"]["agent_id"]
    _set_pace(env, agent_id, "快闪")
    room = make_room(agent_id)
    body = "甲。" * 200
    env.speech.provider_factory = lambda a: RecordingProvider(
        [StreamPiece(CONTENT, body), StreamPiece(END, "", False)]
    )

    msg = env.speech.generate(room["room_id"], agent_id)

    assert msg.content == body
    assert msg.truncated is False, "长于提示词目标却未触发兜底，不该打截断标记"


def test_generate_persists_a_truncated_end(env, seeded, make_room):
    agent_id = seeded["agent"]["agent_id"]
    _set_pace(env, agent_id, "快闪")
    room = make_room(agent_id)
    env.speech.provider_factory = lambda a: RecordingProvider(
        [StreamPiece(CONTENT, "甲。" * 180), StreamPiece(END, "", True)]
    )

    msg = env.speech.generate(room["room_id"], agent_id)

    assert msg.truncated is True
    assert msg.content.endswith("。"), "落库正文须以句界收尾"
