import json
import os
from pathlib import Path

from sqlmodel import Session, select

from app.domain.enums import SourceType
from app.domain.models import ConclusionDoc, HumanMember, Room, RoomAgent, TranscriptExport
from app.models.cutoff import cut_at_boundary
from app.models.demo_provider import DemoProvider
from app.models.provider import CONCLUSION_ASK, CONCLUSION_MARK
from app.services.attribution import annotate
from app.util import new_id

PROPOSITION_KEY = "命题"
KEY_POINTS_KEY = "关键观点"
DISPUTES_KEY = "分歧点"
# 唯一由模型必写的那一章；其余固定章由房间命题与会话记忆确定性给出
SYNTH_FIXED_KEY = "初步结论／建议"
# 键序即章节序：前四键固定，其后为本次讨论归纳出的额外章
FIXED_SECTION_KEYS = [PROPOSITION_KEY, KEY_POINTS_KEY, DISPUTES_KEY, SYNTH_FIXED_KEY]
TRANSCRIPT_MAX_MSGS = 30
TRANSCRIPT_MSG_CHARS = 200
EXTRA_SECTIONS_MAX = 10
SECTION_TITLE_MAX_CHARS = 20
_TITLE_STRIP_CHARS = " \t\r\n\u3000#*"
# 2000 字是写进提示词的目标，由模型自行收束；2400 字是安全界，界内一字不动，
# 超界才在句界收束——为保障语义完整性，稍微超出目标是允许的
SECTION_TARGET_CHARS = 2000
SECTION_MAX_CHARS = 2400
SYNTH_TIMEOUT = 180.0


class EmptyMemoryError(Exception):
    """会话记忆为空，无法生成结论。"""


class SynthesisError(Exception):
    """综合失败：模型调用异常或回复不可解析，本次生成整体拒绝。"""


def build_conclusion_prompt(
    proposition: str, transcript: list[str], key_lines: list[str], dispute_lines: list[str]
) -> list[dict]:
    """综合提示词：记录与已抽取要点都在提问段，离线提供方据此派生可复现的回复。"""
    body = "\n".join(
        [
            f"{CONCLUSION_ASK}命题：{proposition}",
            "讨论记录：",
            *(transcript or ["（无）"]),
            "已抽取关键观点：",
            *(key_lines or ["（无）"]),
            "已抽取分歧点：",
            *(dispute_lines or ["（无）"]),
        ]
    )
    return [
        {
            "role": "system",
            "content": (
                f"{CONCLUSION_MARK}你是头脑风暴房间的会议秘书。据所给讨论记录与已抽取的关键观点、分歧点，"
                f"先写出「{SYNTH_FIXED_KEY}」一章，再按本次讨论的实际内容归纳若干章。\n"
                "额外各章的标题由你依本次讨论拟定，须是这次讨论自己的说法；"
                "「行动项与负责人」「待定项」「风险」与你自己拟的说法同权重，都只是候选，"
                f"用不用只看讨论里有没有对应内容。至多 {EXTRA_SECTIONS_MAX} 章，"
                "只写真有内容支撑的，不为凑数造章；确无值得单列的内容时只回"
                f"「{SYNTH_FIXED_KEY}」一章。标题不得与「{PROPOSITION_KEY}」"
                f"「{KEY_POINTS_KEY}」「{DISPUTES_KEY}」「{SYNTH_FIXED_KEY}」重名，也不得两章同名。\n"
                f"每章不超过 {SECTION_TARGET_CHARS} 字，须是语义完整的段落，不得在句中收住；"
                "自包含、可直接阅读；禁止「见关键观点」「见分歧点」「待指派」一类"
                "指向其他章节或推给主持人的套话；讨论确未涉及某一章时不要写那一章，也不要编造。\n"
                f"只输出一个 JSON 对象：第一个键为「{SYNTH_FIXED_KEY}」，其后依次为额外各章，"
                "键为章标题、值为该章正文；正文内需要引号时一律用「」，不要用半角双引号，"
                "否则会破坏 JSON；JSON 以外不要输出任何内容。"
            ),
        },
        {"role": "user", "content": body},
    ]


def normalize_section_title(raw: str) -> str:
    """归一并校验章标题：去首尾空白与记号，再查长度与重名。

    标题由模型自拟，故先归一再校验，否则「## 风险」与「风险」会被当成两章。
    与固定章名重名是键冲突而不是文体问题：那四键的形态各不相同（命题为字符串、
    关键观点与分歧点为带标注的列表），同名会让同一份 sections 里出现两种形态。
    """
    title = (raw or "").strip(_TITLE_STRIP_CHARS)
    if not title:
        raise ValueError("模型回复的章节标题为空")
    if len(title) > SECTION_TITLE_MAX_CHARS:
        raise ValueError(
            f"章节标题「{title[:SECTION_TITLE_MAX_CHARS]}…」超过 {SECTION_TITLE_MAX_CHARS} 字"
        )
    if title in FIXED_SECTION_KEYS:
        raise ValueError(f"章节标题「{title}」与固定章节重名")
    return title


def _section_text(title: str, value: object) -> str:
    """章正文：须为非空字符串；超安全界时在句界收束，不逐字硬切。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"模型回复的章节「{title}」正文为空或不是字符串")
    kept, _dropped = cut_at_boundary(value.strip(), SECTION_MAX_CHARS)
    return kept


def _escape_inner_quotes(body: str) -> str:
    """给扁平字符串对象里未转义的内层引号补上转义。

    模型常把中文引号写成半角双引号，整份回复因此成为非法 JSON。这里的对象是扁平的
    （键与值都是字符串、无嵌套无数组），故结构引号可由其后一个非空白字符判定：键的
    收尾引号后必是冒号，值的收尾引号后必是逗号或右括号，其余引号都是正文。
    """
    out: list[str] = []
    in_string = False
    is_key = True
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if not in_string:
            out.append(ch)
            in_string = ch == '"'
            i += 1
            continue
        if ch == "\\":
            out.append(body[i : i + 2])
            i += 2
            continue
        if ch == '"':
            j = i + 1
            while j < n and body[j] in " \t\r\n":
                j += 1
            nxt = body[j] if j < n else ""
            closing = nxt == ":" if is_key else nxt in ",}"
            if closing:
                in_string = False
                is_key = not is_key
                out.append(ch)
            else:
                out.append('\\"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_conclusion_reply(text: str) -> dict[str, str]:
    """严格解析综合回复：必写章在且合规、额外章的标题与正文各自合规，否则整体拒绝。

    容许代码围栏与前后赘语，但不猜缺章也不猜标题——把半截回复写进结论文档，
    比拒绝一次更误导主持人。键序即章节序，故按回复里的次序返回。
    """
    body = (text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型回复不是 JSON 对象")
    payload = body[start : end + 1]
    try:
        data = json.loads(payload)
    except ValueError:
        try:
            data = json.loads(_escape_inner_quotes(payload))
        except ValueError as exc:
            raise ValueError("模型回复不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("模型回复不是 JSON 对象")
    if SYNTH_FIXED_KEY not in data:
        raise ValueError(f"模型回复缺少章节「{SYNTH_FIXED_KEY}」")
    out = {SYNTH_FIXED_KEY: _section_text(SYNTH_FIXED_KEY, data[SYNTH_FIXED_KEY])}
    extras = [(k, v) for k, v in data.items() if k != SYNTH_FIXED_KEY]
    if len(extras) > EXTRA_SECTIONS_MAX:
        raise ValueError(f"额外章节 {len(extras)} 个，超过上限 {EXTRA_SECTIONS_MAX} 个")
    for raw_title, value in extras:
        title = normalize_section_title(raw_title)
        if title in out:
            raise ValueError(f"章节标题「{title}」重复")
        out[title] = _section_text(title, value)
    return out


class ConclusionService:
    def __init__(self, engine, config, memory, data_dir: str, provider_factory=None):
        self.engine = engine
        self.config = config
        self.memory = memory
        self.data_dir = data_dir
        self.provider_factory = provider_factory

    def _room(self, room_id: str) -> Room | None:
        with Session(self.engine) as s:
            return s.get(Room, room_id)

    def _source_of(self, agent_id: str) -> dict:
        agent = self.config.get_agent(agent_id)
        kb_names = []
        for kb_id in self.config.bound_kb_ids(agent_id):
            kb = self.config.get_knowledge_base(kb_id)
            if kb:
                kb_names.append(kb.name)
        return {"agent": agent.name if agent else agent_id, "kbs": kb_names}

    def generate(self, room_id: str) -> ConclusionDoc:
        msgs = self.memory.all_messages(room_id)
        if not msgs:
            raise EmptyMemoryError("会话记忆为空，尚不能生成结论")
        room = self._room(room_id)
        proposition = room.proposition if room else ""

        agent_msgs = [m for m in msgs if m.source_type == SourceType.AGENT]
        source_of = {m.source_id: self._source_of(m.source_id) for m in agent_msgs}

        key_points = annotate(
            [{"agent": m.source_id, "point": m.content[:60]} for m in agent_msgs][:6],
            source_of,
        )
        disagreements = []
        seen = set()
        for m in agent_msgs:
            if m.source_id in seen:
                continue
            seen.add(m.source_id)
            stance = self.memory.get_stance(room_id, m.source_id) or m.content[:60]
            disagreements.append({"agent": m.source_id, "point": stance})
        disagreements = annotate(disagreements, source_of)

        names = self._display_names(room_id)
        transcript = [
            f"[{names.get(m.source_id, m.source_id)}] {m.content[:TRANSCRIPT_MSG_CHARS]}"
            for m in msgs[-TRANSCRIPT_MAX_MSGS:]
        ]
        key_lines = [f"- {names.get(it['agent'], it['agent'])}：{it['point']}" for it in key_points]
        dispute_lines = [
            f"- {names.get(it['agent'], it['agent'])}：{it['point']}" for it in disagreements
        ]
        synth = self._synthesize(room_id, proposition, transcript, key_lines, dispute_lines)

        sections: dict = {
            PROPOSITION_KEY: proposition,
            KEY_POINTS_KEY: key_points,
            DISPUTES_KEY: disagreements,
        }
        # synth 的首键即必写章，其后为额外章，update 保持其次序
        sections.update(synth)
        annotations = [kp["source"] for kp in key_points] + [d["source"] for d in disagreements]

        doc_id = new_id("conclusion")
        file_ref = self._write(
            room_id, "conclusion", f"{doc_id}.md", self._render_markdown(proposition, sections)
        )
        doc = ConclusionDoc(
            doc_id=doc_id,
            room_id=room_id,
            proposition=proposition,
            sections=sections,
            source_annotations=annotations,
            file_ref=file_ref,
        )
        with Session(self.engine) as s:
            s.add(doc)
            s.commit()
            s.refresh(doc)
        return doc

    def _synthesize(
        self,
        room_id: str,
        proposition: str,
        transcript: list[str],
        key_lines: list[str],
        dispute_lines: list[str],
    ) -> dict[str, str]:
        """必写章与其后各章交给一次模型综合：套话式的「见××」不是总结，只是把阅读推回前两章。"""
        conn_id, model_name = self._synthesis_target(room_id)
        factory = self.provider_factory or (lambda _cid: DemoProvider())
        try:
            reply = factory(conn_id).chat(
                model_name,
                build_conclusion_prompt(proposition, transcript, key_lines, dispute_lines),
                None,
                timeout=SYNTH_TIMEOUT,
                extra_body={"enable_thinking": False},
            )
        except Exception as exc:
            # 提供方异常种类不一（网络、鉴权、超时）；综合失败一律同口径拒绝，可重试
            raise SynthesisError(f"结论综合的模型调用失败：{exc}") from exc
        try:
            return parse_conclusion_reply(reply)
        except ValueError as exc:
            raise SynthesisError(f"结论综合失败：{exc}") from exc

    def _synthesis_target(self, room_id: str) -> tuple[str, str]:
        conn_id, model_name = self.config.get_conclusion_model()
        conn = self.config.get_connection(conn_id) if conn_id else None
        # 设置可能指向已删除的连接或已下线的模型名：回落而不是报错，综合不该被配置漂移挡住
        if conn and model_name in (conn.available_models or []):
            return conn_id, model_name
        with Session(self.engine) as s:
            row = s.exec(
                select(RoomAgent)
                .where(RoomAgent.room_id == room_id)
                .order_by(RoomAgent.joined_at)
            ).first()
        agent = self.config.get_agent(row.agent_id) if row else None
        if agent is None:
            raise SynthesisError("房间没有可用的智能体可作结论综合的回落目标")
        return agent.model_connection_id, agent.model_name

    def _display_names(self, room_id: str) -> dict[str, str]:
        """一次读出房间全部人类成员与全部智能体的显示名，供整份导出复用，避免逐条查库。"""
        names: dict[str, str] = {}
        with Session(self.engine) as s:
            rows = s.exec(select(HumanMember).where(HumanMember.room_id == room_id)).all()
            for m in rows:
                names[m.member_id] = m.display_name
        for agent in self.config.list_agents():
            names[agent.agent_id] = agent.name
        return names

    def export_transcript(self, room_id: str) -> TranscriptExport:
        msgs = self.memory.all_messages(room_id)
        names = self._display_names(room_id)
        lines = []
        for m in msgs:
            if m.source_type == SourceType.AGENT:
                who = names.get(m.source_id, m.source_id)
            else:
                who = names.get(m.source_id, "主持人" if m.source_id == "host" else "人类成员")
            targets = [names.get(a, a) for a in (m.addressee_agent_ids or [])]
            # 智能体在前、成员在后，与界面受话者标签同序
            targets += [names.get(x, x) for x in (m.addressee_member_ids or [])]
            to = f" → {'、'.join(targets)}" if targets else ""
            cut = "（篇幅所限，未展开）" if m.truncated else ""
            lines.append(
                f"[{m.created_at.isoformat()}] {m.source_type.value}·{who}{to} {m.content}{cut}"
            )
        export_id = new_id("export")
        file_ref = self._write(room_id, "transcript", f"{export_id}.txt", "\n".join(lines))
        exp = TranscriptExport(
            export_id=export_id, room_id=room_id, entry_count=len(msgs), file_ref=file_ref
        )
        with Session(self.engine) as s:
            s.add(exp)
            s.commit()
            s.refresh(exp)
        return exp

    def latest_conclusion(self, room_id: str) -> ConclusionDoc | None:
        with Session(self.engine) as s:
            return s.exec(
                select(ConclusionDoc)
                .where(ConclusionDoc.room_id == room_id)
                .order_by(ConclusionDoc.created_at.desc())
            ).first()

    def get_conclusion(self, doc_id: str) -> ConclusionDoc | None:
        with Session(self.engine) as s:
            return s.get(ConclusionDoc, doc_id)

    def latest_export(self, room_id: str) -> TranscriptExport | None:
        with Session(self.engine) as s:
            return s.exec(
                select(TranscriptExport)
                .where(TranscriptExport.room_id == room_id)
                .order_by(TranscriptExport.created_at.desc())
            ).first()

    def get_export(self, export_id: str) -> TranscriptExport | None:
        with Session(self.engine) as s:
            return s.get(TranscriptExport, export_id)

    def resolve_artifact(self, file_ref: str) -> Path | None:
        base = Path(self.data_dir).resolve()
        target = (base / file_ref).resolve()
        if base not in target.parents and target != base:
            return None
        return target if target.is_file() else None

    def _render_markdown(self, proposition: str, sections: dict) -> str:
        out = [f"# 阶段结论\n\n**命题**：{proposition}\n"]
        for key, value in sections.items():
            if key == PROPOSITION_KEY:
                continue  # 命题只作文档头，不再重复成一章
            out.append(f"## {key}")
            if isinstance(value, list):
                for item in value:
                    src = item.get("source", {})
                    # 来源标注只呈现智能体名；知识库保留在来源标注数据里供追溯
                    src_txt = f"（{src.get('agent')}）"
                    out.append(f"- {item.get('point')} {src_txt}")
            else:
                out.append(f"{value}")
            out.append("")
        return "\n".join(out)

    def _write(self, room_id: str, sub: str, name: str, content: str) -> str:
        rel = f"rooms/{room_id}/{sub}/{name}"
        path = Path(self.data_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return rel
