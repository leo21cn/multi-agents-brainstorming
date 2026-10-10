from sqlmodel import Session

from app.domain.enums import SourceType
from app.domain.models import Room


class SpeechError(Exception):
    """发言生成失败（模型调用异常等）。"""


class SpeechService:
    def __init__(self, engine, config, memory, retrieval, provider_factory):
        self.engine = engine
        self.config = config
        self.memory = memory
        self.retrieval = retrieval
        self.provider_factory = provider_factory

    def _proposition(self, room_id: str) -> str:
        with Session(self.engine) as s:
            room = s.get(Room, room_id)
        return room.proposition if room else ""

    def generate(self, room_id: str, agent_id: str, intent: str | None = None):
        agent = self.config.get_agent(agent_id)
        if agent is None:
            raise SpeechError("agent not found")
        kb_ids = self.config.bound_kb_ids(agent_id)
        query = intent or agent.name
        hits = self.retrieval.retrieve(query, kb_ids) if kb_ids else []
        context = self.memory.build_context(
            self._proposition(room_id), room_id, agent_id, kb_hits=hits
        )
        card = self.config.get_role_card(agent.role_card_id)
        persona = card.persona if card else ""
        expertise = card.expertise if card else ""
        system = f"你是{agent.name}。{persona} 专长：{expertise}"
        messages = [{"role": "system", "content": system}] + context
        provider = self.provider_factory(agent)
        try:
            content = provider.chat(agent.model_name, messages)
        except Exception as exc:  # 模型超时/失败
            raise SpeechError(str(exc))
        cited = sorted({h.kb_id for h in hits})
        msg = self.memory.append_message(
            room_id,
            SourceType.AGENT,
            agent_id,
            content,
            cited_kb_ids=cited,
            no_kb_citation=len(hits) == 0,
        )
        self.memory.update_stance(room_id, agent_id, content[:200])
        self.memory.maybe_summarize(room_id)
        return msg
