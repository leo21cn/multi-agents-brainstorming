import logging
from typing import Iterator

from sqlmodel import Session, select

from app.domain.enums import RoomStatus, SourceType
from app.domain.models import HumanMember, Message, Room
from app.models.provider import CONTENT, END, NOTICE, THOUGHT
from app.models.thinking import resolve

logger = logging.getLogger(__name__)

STAGE_RETRIEVE = "检索知识库"
STAGE_THINK = "思考中"
STAGE_GENERATE = "生成中"


class SpeechError(Exception):
    """发言生成失败（模型调用异常等）。"""


class RoomClosedError(SpeechError):
    """房间已结束后不再新增发言。"""


def build_system_prompt(
    name: str, persona: str, expertise: str, speaking_style: str | None, max_chars: int
) -> str:
    """组装发言提示词。首句须为「你是{name}。」——离线提供方以句号切分取智能体名。

    要求段排在风格之后是刻意的：长度与结构一律由档位要求管，风格只决定视角与语气，
    否则风格里写「详细展开、逐条论证」而档位选快闪时两条指令互相打架。

    字数目标是交给模型自行收束的范围，客户端只在其上浮的安全界处兜底断流（见
    app/models/cutoff.py），故要求段须同时写明以完整句收束——长度不该以半句为代价。
    """
    lines = [
        f"你是{name}。",
        "你是本次头脑风暴的参与者之一。",
        persona,
        f"专长：{expertise}",
    ]
    if speaking_style:
        lines.append(f"发言风格：{speaking_style}")
    lines += [
        "",
        "【本次发言要求】",
        f"- 只给观点，不写文章，正文 {max_chars} 字以内，以完整句收束：不写半句、不留未收尾的列举，说不完的观点宁可不提。",
        "- 先亮判断，再给一条最关键的依据；不铺陈背景，不复述他人已说过的内容。",
        "- 观点日后可被他人修正，但本次发言自身须是一段说得完的话。",
        "- 与他人观点冲突时直接指出冲突点，不做调和式总结。",
    ]
    return "\n".join(lines)


class SpeechService:
    def __init__(self, engine, config, memory, retrieval, provider_factory):
        self.engine = engine
        self.config = config
        self.memory = memory
        self.retrieval = retrieval
        self.provider_factory = provider_factory

    def _room(self, room_id: str) -> Room | None:
        with Session(self.engine) as s:
            return s.get(Room, room_id)

    def asker_name(self, room_id: str) -> str:
        """提问方即房间主持人的显示名；标识或成员缺失时回退通称。"""
        with Session(self.engine) as s:
            room = s.get(Room, room_id)
            host_id = room.host_member_id if room else None
            if not host_id:
                return "主持人"
            member = s.get(HumanMember, host_id)
            return member.display_name if member else "主持人"

    def _human_names(self, room_id: str) -> dict[str, str]:
        """房间内全部人类成员的显示名：智能体与导出都要能分辨是哪个人说的话。"""
        with Session(self.engine) as s:
            rows = s.exec(select(HumanMember).where(HumanMember.room_id == room_id)).all()
            return {m.member_id: m.display_name for m in rows}

    def _focus_of(
        self, room_id: str, agent_id: str, asker: str, directed_from: str | None = None
    ) -> str | None:
        """L0 当前焦点：待答提问的原文。

        多个受话者共用同一条提问，轮到第二个人时它已不是房间最后一条，故焦点须由消息
        标识锚定。无锚点时回退「最后一条是定向给本次发言者的人类消息」的启发式，REST
        一次性路径与放行举手的行为与既有实现一致。
        """
        if directed_from is not None:
            ask = self.memory.get_message(directed_from)
        else:
            msgs = self.memory.all_messages(room_id)
            ask = msgs[-1] if msgs else None
        if (
            ask is None
            or ask.source_type != SourceType.HUMAN
            or agent_id not in (ask.addressee_agent_ids or [])
        ):
            return None
        return f"{asker}向你提问：{ask.content}"

    def _prepare(
        self, room_id: str, agent_id: str, intent: str | None, directed_from: str | None = None
    ):
        room = self._room(room_id)
        if room is not None and room.status == RoomStatus.ENDED:
            raise RoomClosedError("房间已结束，不再新增发言")
        agent = self.config.get_agent(agent_id)
        if agent is None:
            raise SpeechError("agent not found")
        kb_ids = self.config.bound_kb_ids(agent_id)
        query = intent or agent.name
        try:
            if kb_ids:
                hits, degraded = self.retrieval.retrieve_combined(query, kb_ids)
                if degraded:
                    # 显式降级只进日志：不进上下文、不进界面，来源标注口径不变
                    logger.warning("external kb degraded: %s", "; ".join(degraded))
            else:
                hits = []
        except Exception as exc:  # 嵌入端点不可达、向量维度不匹配等
            raise SpeechError(f"检索失败：{exc}")
        asker = self.asker_name(room_id)
        human_names = self._human_names(room_id)

        def name_of(source_type, source_id: str) -> str:
            if source_type == SourceType.AGENT:
                other = self.config.get_agent(source_id)
                return other.name if other is not None else source_id
            # 字面值 "host" 是实施前落库的历史消息，迁移不改写它，继续按主持人回落
            if source_id == "host":
                return asker
            return human_names.get(source_id, "人类成员")

        context = self.memory.build_context(
            room.proposition if room else "",
            room_id,
            agent_id,
            kb_hits=hits,
            name_of=name_of,
            focus=self._focus_of(room_id, agent_id, asker, directed_from),
        )
        pace = resolve(agent.speech_pace, agent.model_name)
        system = build_system_prompt(
            agent.name,
            agent.persona,
            agent.expertise,
            agent.speaking_style,
            pace.max_chars,
        )
        return agent, hits, [{"role": "system", "content": system}] + context, pace

    def _commit(
        self, room_id: str, agent_id: str, content: str, hits, truncated: bool = False
    ) -> Message:
        cited = sorted({h.kb_id for h in hits})
        msg = self.memory.append_message(
            room_id,
            SourceType.AGENT,
            agent_id,
            content,
            cited_kb_ids=cited,
            no_kb_citation=len(hits) == 0,
            truncated=truncated,
        )
        self.memory.update_stance(room_id, agent_id, content[:200])
        self.memory.maybe_summarize(room_id)
        return msg

    def generate(
        self,
        room_id: str,
        agent_id: str,
        intent: str | None = None,
        directed_from: str | None = None,
    ) -> Message:
        """一次性发言：消费流式实现以取得截断标记。

        断流点改按句界落点后，正文长度不再与截断等价，故标记只能取自 END 片段。
        """
        agent, hits, messages, pace = self._prepare(room_id, agent_id, intent, directed_from)
        provider = self.provider_factory(agent)
        parts: list[str] = []
        truncated = False
        try:
            for piece in provider.stream_chat(agent.model_name, messages, pace):
                if piece.kind == CONTENT:
                    parts.append(piece.text)
                elif piece.kind == END:
                    truncated = piece.truncated
        except Exception as exc:  # 模型超时/失败
            raise SpeechError(str(exc))
        return self._commit(room_id, agent_id, "".join(parts), hits, truncated)

    def generate_stream(
        self,
        room_id: str,
        agent_id: str,
        intent: str | None = None,
        directed_from: str | None = None,
    ) -> Iterator[tuple[str, object]]:
        """产出 ("stage", 文案)／("thought", 增量)／("delta", 增量)／("message", 已落库消息)。

        检索与上下文组装在首个增量之前完成，故先给出阶段反馈；生成中失败抛
        SpeechError 且不落库部分内容。

        思考增量独立成类：不落库、不进上下文，仅供界面折叠呈现，故 A-12「落库内容与
        增量拼接一致」只对 delta 成立。「思考中」阶段惰性下发——首个思考块到达才发，
        离线提供方不产思考块，预先下发会让界面显示一个永远等不到内容的阶段。
        """
        yield "stage", STAGE_RETRIEVE
        agent, hits, messages, pace = self._prepare(room_id, agent_id, intent, directed_from)
        provider = self.provider_factory(agent)
        pending_think = pace.thinking_enabled
        pending_generate = True
        if not pace.thinking_enabled:
            yield "stage", STAGE_GENERATE
            pending_generate = False
        parts: list[str] = []
        truncated = False
        try:
            for piece in provider.stream_chat(agent.model_name, messages, pace):
                if piece.kind == THOUGHT:
                    if not piece.text:
                        continue
                    if pending_think:
                        yield "stage", STAGE_THINK
                        pending_think = False
                    yield "thought", piece.text
                elif piece.kind == NOTICE:
                    # 降级须可见；重试路径仍欠一个生成中阶段，故不复位 pending_generate
                    yield "stage", piece.text
                elif piece.kind == CONTENT:
                    if not piece.text:
                        continue
                    if pending_generate:
                        yield "stage", STAGE_GENERATE
                        pending_generate = False
                    parts.append(piece.text)
                    yield "delta", piece.text
                elif piece.kind == END:
                    truncated = piece.truncated
        except Exception as exc:  # 模型超时/失败
            raise SpeechError(str(exc))
        yield "message", self._commit(room_id, agent_id, "".join(parts), hits, truncated)
