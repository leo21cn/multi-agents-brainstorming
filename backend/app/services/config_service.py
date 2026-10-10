from sqlmodel import Session, select

from app.domain.enums import DocumentStatus
from app.domain.models import (
    Agent,
    AgentKnowledgeBase,
    Chunk,
    Document,
    KnowledgeBase,
    ModelConnection,
    RoleCard,
)
from app.rag import embeddings
from app.rag.splitter import split_text
from app.util import new_id


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return key[:3] + "•" * 8 + key[-4:]


class ConfigService:
    def __init__(self, engine, vector_store=None):
        self.engine = engine
        self.vs = vector_store

    # ---------- 模型连接 ----------
    def create_connection(self, data) -> ModelConnection:
        conn = ModelConnection(
            conn_id=new_id("conn"),
            name=data.name,
            provider_type=data.provider_type,
            base_url=data.base_url,
            api_key=data.api_key,
            available_models=list(data.available_models),
        )
        with Session(self.engine) as s:
            s.add(conn)
            s.commit()
            s.refresh(conn)
        return conn

    def list_connections(self) -> list[ModelConnection]:
        with Session(self.engine) as s:
            return list(s.exec(select(ModelConnection)).all())

    def get_connection(self, conn_id: str) -> ModelConnection | None:
        with Session(self.engine) as s:
            return s.get(ModelConnection, conn_id)

    def update_connection(self, conn_id: str, data) -> ModelConnection | None:
        with Session(self.engine) as s:
            conn = s.get(ModelConnection, conn_id)
            if conn is None:
                return None
            for key, value in data.model_dump(exclude_none=True).items():
                setattr(conn, key, value)
            s.add(conn)
            s.commit()
            s.refresh(conn)
            return conn

    # ---------- 知识库与文档 ----------
    def create_knowledge_base(self, name: str, dimension) -> KnowledgeBase:
        kb = KnowledgeBase(kb_id=new_id("kb"), name=name, dimension=dimension)
        with Session(self.engine) as s:
            s.add(kb)
            s.commit()
            s.refresh(kb)
        return kb

    def get_or_create_knowledge_base(self, name: str, dimension) -> KnowledgeBase:
        with Session(self.engine) as s:
            row = s.exec(select(KnowledgeBase).where(KnowledgeBase.name == name)).first()
        return row if row else self.create_knowledge_base(name, dimension)

    def list_knowledge_bases(self) -> list[KnowledgeBase]:
        with Session(self.engine) as s:
            return list(s.exec(select(KnowledgeBase)).all())

    def get_knowledge_base(self, kb_id: str) -> KnowledgeBase | None:
        with Session(self.engine) as s:
            return s.get(KnowledgeBase, kb_id)

    def ingest_document(
        self, kb_id: str, file_name: str, format: str, text: str, size: int = 0
    ) -> Document:
        if self.get_knowledge_base(kb_id) is None:
            raise ValueError("knowledge base not found")
        content = (text or "").strip()
        if not content:
            raise ValueError("empty document")
        chunks = split_text(content)
        vectors = embeddings.embed_texts(chunks)
        doc_id = new_id("doc")
        ids = [f"{doc_id}-{i}" for i in range(len(chunks))]
        if self.vs is not None:
            self.vs.add(kb_id, ids, chunks, vectors)
        doc = Document(
            doc_id=doc_id,
            kb_id=kb_id,
            file_name=file_name,
            format=format,
            size=size,
            status=DocumentStatus.INGESTED,
            file_ref=f"knowledge_base/{kb_id}/{doc_id}",
        )
        with Session(self.engine) as s:
            s.add(doc)
            for i, (cid, t) in enumerate(zip(ids, chunks)):
                s.add(Chunk(chunk_id=cid, doc_id=doc_id, seq=i, text=t, vector_ref=cid))
            s.commit()
            s.refresh(doc)
        return doc

    def list_documents(self, kb_id: str | None = None) -> list[Document]:
        with Session(self.engine) as s:
            stmt = select(Document)
            if kb_id:
                stmt = stmt.where(Document.kb_id == kb_id)
            return list(s.exec(stmt).all())

    # ---------- 角色卡 ----------
    def create_role_card(self, name, persona, expertise, speaking_style=None) -> RoleCard:
        card = RoleCard(
            card_id=new_id("card"),
            name=name,
            persona=persona,
            expertise=expertise,
            speaking_style=speaking_style,
        )
        with Session(self.engine) as s:
            s.add(card)
            s.commit()
            s.refresh(card)
        return card

    def list_role_cards(self) -> list[RoleCard]:
        with Session(self.engine) as s:
            return list(s.exec(select(RoleCard)).all())

    def get_role_card(self, card_id: str) -> RoleCard | None:
        with Session(self.engine) as s:
            return s.get(RoleCard, card_id)

    # ---------- 智能体 ----------
    def create_agent(
        self, name, model_connection_id, model_name, kb_ids, role_card_id
    ) -> Agent:
        conn = self.get_connection(model_connection_id)
        if conn is None:
            raise ValueError("model connection not found")
        if model_name not in conn.available_models:
            raise ValueError("model not in connection available models")
        if not kb_ids:
            raise ValueError("at least one knowledge base required")
        for kb_id in kb_ids:
            if self.get_knowledge_base(kb_id) is None:
                raise ValueError(f"knowledge base not found: {kb_id}")
        if self.get_role_card(role_card_id) is None:
            raise ValueError("role card not found")
        agent = Agent(
            agent_id=new_id("agent"),
            name=name,
            model_connection_id=model_connection_id,
            model_name=model_name,
            role_card_id=role_card_id,
        )
        with Session(self.engine) as s:
            s.add(agent)
            for kb_id in kb_ids:
                s.add(AgentKnowledgeBase(agent_id=agent.agent_id, kb_id=kb_id))
            s.commit()
            s.refresh(agent)
        return agent

    def list_agents(self) -> list[Agent]:
        with Session(self.engine) as s:
            return list(s.exec(select(Agent)).all())

    def get_agent(self, agent_id: str) -> Agent | None:
        with Session(self.engine) as s:
            return s.get(Agent, agent_id)

    def bound_kb_ids(self, agent_id: str) -> list[str]:
        with Session(self.engine) as s:
            rows = s.exec(
                select(AgentKnowledgeBase).where(AgentKnowledgeBase.agent_id == agent_id)
            ).all()
            return [r.kb_id for r in rows]

    def update_agent(self, agent_id: str, data) -> Agent | None:
        if data.model_name is not None and data.model_connection_id is not None:
            if data.model_name not in self.get_connection(data.model_connection_id).available_models:
                raise ValueError("model not in connection available models")
        with Session(self.engine) as s:
            agent = s.get(Agent, agent_id)
            if agent is None:
                return None
            for key, value in data.model_dump(exclude_none=True).items():
                if key == "kb_ids":
                    continue
                setattr(agent, key, value)
            if data.kb_ids is not None:
                for row in s.exec(
                    select(AgentKnowledgeBase).where(AgentKnowledgeBase.agent_id == agent_id)
                ).all():
                    s.delete(row)
                for kb_id in data.kb_ids:
                    s.add(AgentKnowledgeBase(agent_id=agent_id, kb_id=kb_id))
            s.add(agent)
            s.commit()
            s.refresh(agent)
            return agent

    # ---------- 初始化状态 ----------
    def init_status(self) -> dict:
        kb_count = len(self.list_knowledge_bases())
        agents = self.list_agents()
        bound = sum(1 for a in agents if self.bound_kb_ids(a.agent_id))
        return {
            "kb_count": kb_count,
            "bound_agent_count": bound,
            "ready": kb_count >= 1 and bound >= 1,
        }
