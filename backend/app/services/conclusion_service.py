import os
from pathlib import Path

from sqlmodel import Session

from app.domain.enums import SourceType
from app.domain.models import ConclusionDoc, Room, TranscriptExport
from app.services.attribution import annotate
from app.util import new_id

SECTION_KEYS = [
    "命题",
    "关键观点",
    "分歧点",
    "初步结论／建议",
    "行动项与负责人",
    "待定项",
    "风险",
]


class EmptyMemoryError(Exception):
    """会话记忆为空，无法生成结论。"""


class ConclusionService:
    def __init__(self, engine, config, memory, data_dir: str):
        self.engine = engine
        self.config = config
        self.memory = memory
        self.data_dir = data_dir

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
            raise EmptyMemoryError("conversation memory is empty")
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

        sections = {
            "命题": proposition,
            "关键观点": key_points,
            "分歧点": disagreements,
            "初步结论／建议": "见关键观点与分歧点；需主持人确认后收敛。",
            "行动项与负责人": "待指派。",
            "待定项": "见分歧点。",
            "风险": "见分歧点与待定项。",
        }
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

    def export_transcript(self, room_id: str) -> TranscriptExport:
        msgs = self.memory.all_messages(room_id)
        lines = [
            f"[{m.created_at.isoformat()}] {m.source_type.value}:{m.source_id} {m.content}"
            for m in msgs
        ]
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

    def _render_markdown(self, proposition: str, sections: dict) -> str:
        out = [f"# 阶段结论\n\n**命题**：{proposition}\n"]
        for key in SECTION_KEYS:
            out.append(f"## {key}")
            value = sections[key]
            if isinstance(value, list):
                for item in value:
                    src = item.get("source", {})
                    src_txt = f"（{src.get('agent')} · {'、'.join(src.get('kbs', []))}）"
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
