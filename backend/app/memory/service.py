from sqlmodel import Session, select

from app.domain.enums import SourceType
from app.domain.models import Message, StanceMem, Summary
from app.memory.compress import RollingSummary
from app.memory.context import assemble_context
from app.util import new_id


class MemoryService:
    def __init__(self, engine, ctx_budget: int = 6000, summary_trigger_tokens: int = 8000):
        self.engine = engine
        self.ctx_budget = ctx_budget
        self.summary_trigger_tokens = summary_trigger_tokens
        self._summary = RollingSummary()

    def append_message(
        self,
        room_id: str,
        source_type: SourceType,
        source_id: str,
        content: str,
        cited_kb_ids: list[str] | None = None,
        no_kb_citation: bool = False,
        addressee_agent_ids: list[str] | None = None,
        addressee_member_ids: list[str] | None = None,
        truncated: bool = False,
    ) -> Message:
        msg = Message(
            message_id=new_id("msg"),
            room_id=room_id,
            source_type=source_type,
            source_id=source_id,
            content=content,
            cited_kb_ids=list(cited_kb_ids or []),
            no_kb_citation=no_kb_citation,
            addressee_agent_ids=list(addressee_agent_ids or []),
            addressee_member_ids=list(addressee_member_ids or []),
            truncated=truncated,
        )
        with Session(self.engine) as s:
            s.add(msg)
            s.commit()
            s.refresh(msg)
        return msg

    def get_message(self, message_id: str) -> Message | None:
        with Session(self.engine) as s:
            return s.get(Message, message_id)

    def all_messages(self, room_id: str) -> list[Message]:
        with Session(self.engine) as s:
            return list(
                s.exec(
                    select(Message)
                    .where(Message.room_id == room_id)
                    .order_by(Message.created_at)
                ).all()
            )

    def latest_summary(self, room_id: str) -> str | None:
        with Session(self.engine) as s:
            row = s.exec(
                select(Summary)
                .where(Summary.room_id == room_id)
                .order_by(Summary.created_at.desc())
            ).first()
            return row.content if row else None

    def get_stance(self, room_id: str, agent_id: str) -> str | None:
        with Session(self.engine) as s:
            row = s.get(StanceMem, f"{room_id}:{agent_id}")
            return row.content if row else None

    def update_stance(self, room_id: str, agent_id: str, text: str) -> None:
        key = f"{room_id}:{agent_id}"
        with Session(self.engine) as s:
            row = s.get(StanceMem, key)
            if row is None:
                row = StanceMem(stance_id=key, room_id=room_id, agent_id=agent_id, content=text)
            else:
                row.content = text
            s.add(row)
            s.commit()

    def maybe_summarize(self, room_id: str) -> Summary | None:
        msgs = self.all_messages(room_id)
        total = sum(len(m.content) for m in msgs)
        if total < self.summary_trigger_tokens:
            return None
        content = self._summary.compress(msgs)
        row = Summary(
            summary_id=new_id("sum"),
            room_id=room_id,
            covered_from=msgs[0].message_id,
            covered_to=msgs[-1].message_id,
            content=content,
            token_count=total,
        )
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        return row

    def build_context(
        self,
        proposition: str,
        room_id: str,
        agent_id: str,
        kb_hits=None,
        name_of=None,
        focus: str | None = None,
    ) -> list[dict]:
        recent = []
        for m in self.all_messages(room_id)[-20:]:
            content = (
                m.content
                if name_of is None
                else f"[{name_of(m.source_type, m.source_id)}·{m.source_type.value}] {m.content}"
            )
            recent.append({"role": "assistant", "content": content})
        return assemble_context(
            proposition=proposition,
            l2_summary=self.latest_summary(room_id),
            recent_messages=recent,
            stance=self.get_stance(room_id, agent_id),
            kb_hits=kb_hits,
            budget=self.ctx_budget,
            focus=focus,
        )
