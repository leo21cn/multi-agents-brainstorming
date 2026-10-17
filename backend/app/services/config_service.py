from sqlmodel import Session, select

from app.domain.enums import DocumentStatus, SpeechPace
from app.domain.models import (
    Agent,
    AgentKnowledgeBase,
    Chunk,
    Document,
    ExternalCorpusLink,
    KnowledgeBase,
    ModelConnection,
    RoomAgent,
    SystemSetting,
    _now,
)
from app.rag import embeddings
from app.rag.splitter import split_text
from app.util import new_id

DEFAULT_ADMIN_TOKEN = "111111"
ADMIN_TOKEN_KEY = "admin_token"
SHOW_RAISE_SIM_KEY = "show_raise_sim"
AUTO_RAISE_KEY = "auto_raise"
CONCLUSION_CONN_KEY = "conclusion_conn_id"
CONCLUSION_MODEL_KEY = "conclusion_model"
ASR_CONN_KEY = "asr_conn_id"
ASR_MODEL_KEY = "asr_model"
EXT_KB_URL_KEY = "ext_kb_url"
EXT_KB_TOKEN_KEY = "ext_kb_token"
EXT_KB_ENABLED_KEY = "ext_kb_enabled"


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return key[:3] + "•" * 8 + key[-4:]


class InUseError(Exception):
    """目标被引用，不可删除。"""


class AliasConflictError(Exception):
    """挂接别名冲突：与既有别名/知识库名/智能体名重名。"""


class ConfigService:
    def __init__(self, engine, vector_store=None):
        self.engine = engine
        self.vs = vector_store

    # ---------- 系统设置与管理员令牌 ----------
    def get_setting(self, key: str) -> str | None:
        with Session(self.engine) as s:
            row = s.get(SystemSetting, key)
            return row.value if row else None

    def set_setting(self, key: str, value: str) -> None:
        with Session(self.engine) as s:
            row = s.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(key=key, value=value)
            else:
                row.value = value
                row.updated_at = _now()
            s.add(row)
            s.commit()

    def ensure_admin_token(self) -> str:
        """确保管理员令牌存在；缺省时写入默认值并返回。"""
        token = self.get_setting(ADMIN_TOKEN_KEY)
        if token is None:
            self.set_setting(ADMIN_TOKEN_KEY, DEFAULT_ADMIN_TOKEN)
            return DEFAULT_ADMIN_TOKEN
        return token

    def get_admin_token(self) -> str:
        token = self.get_setting(ADMIN_TOKEN_KEY)
        return token if token is not None else DEFAULT_ADMIN_TOKEN

    def set_admin_token(self, new_token: str) -> None:
        value = (new_token or "").strip()
        if not value:
            raise ValueError("admin token must not be empty")
        # 令牌经 HTTP 头传输，非 ASCII 值无法编码，会导致管理界面无法再发出任何请求
        if not value.isascii():
            raise ValueError("admin token must be ASCII")
        self.set_setting(ADMIN_TOKEN_KEY, value)

    def get_show_raise_sim(self) -> bool:
        """模拟举手入口缺省显示：未播种的既有库读取时按真处理，不做迁移。"""
        return self.get_setting(SHOW_RAISE_SIM_KEY) != "0"

    def set_show_raise_sim(self, shown: bool) -> None:
        self.set_setting(SHOW_RAISE_SIM_KEY, "1" if shown else "0")

    def get_auto_raise(self) -> bool:
        """自主举手缺省开：未播种的既有库读取时按开处理，不做迁移。"""
        return self.get_setting(AUTO_RAISE_KEY) != "0"

    def set_auto_raise(self, enabled: bool) -> None:
        self.set_setting(AUTO_RAISE_KEY, "1" if enabled else "0")

    def get_conclusion_model(self) -> tuple[str | None, str | None]:
        """结论综合的连接与模型名；留空表示回落房间阵容首位智能体，既有库无需迁移。"""
        conn_id = self.get_setting(CONCLUSION_CONN_KEY) or None
        model = self.get_setting(CONCLUSION_MODEL_KEY) or None
        return conn_id, model

    def set_conclusion_model(self, conn_id: str | None, model_name: str | None) -> None:
        conn_id = (conn_id or "").strip() or None
        model_name = (model_name or "").strip() or None
        if bool(conn_id) != bool(model_name):
            raise ValueError("结论综合的连接与模型名须同时给出或同时留空")
        if conn_id is not None:
            conn = self.get_connection(conn_id)
            if conn is None:
                raise ValueError("model connection not found")
            if model_name not in (conn.available_models or []):
                raise ValueError("模型名不在该连接的可用模型列表内")
        self.set_setting(CONCLUSION_CONN_KEY, conn_id or "")
        self.set_setting(CONCLUSION_MODEL_KEY, model_name or "")

    def get_asr_model(self) -> tuple[str | None, str | None]:
        """语音转写的连接与模型名；留空表示未配置（语音按钮给出引导），既有库无需迁移。"""
        conn_id = self.get_setting(ASR_CONN_KEY) or None
        model = self.get_setting(ASR_MODEL_KEY) or None
        return conn_id, model

    def set_asr_model(self, conn_id: str | None, model_name: str | None) -> None:
        conn_id = (conn_id or "").strip() or None
        model_name = (model_name or "").strip() or None
        if bool(conn_id) != bool(model_name):
            raise ValueError("语音转写的连接与模型名须同时给出或同时留空")
        if conn_id is not None:
            if self.get_connection(conn_id) is None:
                raise ValueError("model connection not found")
            # 转写模型名是自由文本（如 qwen-audio-3.0-asr-flash），不校验可用对话模型列表
        self.set_setting(ASR_CONN_KEY, conn_id or "")
        self.set_setting(ASR_MODEL_KEY, model_name or "")

    # ---------- 外部中心知识库连接 ----------
    def get_external_kb(self) -> dict:
        """连接配置（URL/令牌/启用）。令牌受保护：调用方只取回掩码，不下发明文。"""
        return {
            "url": self.get_setting(EXT_KB_URL_KEY) or "",
            "token": self.get_setting(EXT_KB_TOKEN_KEY) or "",
            "enabled": self.get_setting(EXT_KB_ENABLED_KEY) == "1",
        }

    def set_external_kb(self, url: str | None, token: str | None, enabled: bool) -> None:
        url = (url or "").strip().rstrip("/")
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("外部知识库地址须以 http:// 或 https:// 开头")
        current_token = self.get_setting(EXT_KB_TOKEN_KEY) or ""
        # token 为 None/空串表示不修改（界面留空即此语义）
        token_unchanged = token is None or not token.strip()
        effective = current_token if token_unchanged else token.strip()
        if token is not None and token.strip():
            if not token.strip().isascii():
                raise ValueError("外部知识库令牌须为 ASCII 可打印字符")
        if enabled and (not url or not effective):
            raise ValueError("启用外部知识库时地址与令牌不能为空")
        if not token_unchanged:
            self.set_setting(EXT_KB_TOKEN_KEY, effective)
        self.set_setting(EXT_KB_URL_KEY, url)
        self.set_setting(EXT_KB_ENABLED_KEY, "1" if enabled else "")

    # ---------- 外部语料库挂接 ----------
    def list_external_links(self, kb_id: str | None = None) -> list[ExternalCorpusLink]:
        with Session(self.engine) as s:
            stmt = select(ExternalCorpusLink)
            if kb_id is not None:
                stmt = stmt.where(ExternalCorpusLink.kb_id == kb_id)
            return list(s.exec(stmt).all())

    def get_external_link(self, kb_id: str, link_id: str) -> ExternalCorpusLink | None:
        with Session(self.engine) as s:
            return s.exec(
                select(ExternalCorpusLink).where(
                    ExternalCorpusLink.kb_id == kb_id,
                    ExternalCorpusLink.link_id == link_id,
                )
            ).first()

    def alias_conflict(self, alias: str) -> str | None:
        """别名冲突检查：与既有挂接别名、知识库名、智能体名全局不重。返回冲突描述或 None。"""
        alias = (alias or "").strip()
        for kb in self.list_knowledge_bases():
            if kb.name == alias:
                return f"与知识库「{alias}」重名"
        for agent in self.list_agents():
            if agent.name == alias:
                return f"与智能体「{alias}」重名"
        with Session(self.engine) as s:
            row = s.exec(
                select(ExternalCorpusLink).where(ExternalCorpusLink.alias == alias)
            ).first()
        if row is not None:
            return "别名已被占用"
        return None

    def add_external_link(self, kb_id: str, ext_corpus_id: str, alias: str) -> ExternalCorpusLink:
        if self.get_knowledge_base(kb_id) is None:
            raise ValueError("knowledge base not found")
        alias = (alias or "").strip()
        if not alias or len(alias) > 40:
            raise ValueError("别名不能为空且不超过 40 字")
        conflict = self.alias_conflict(alias)
        if conflict is not None:
            raise AliasConflictError(conflict)
        with Session(self.engine) as s:
            dup = s.exec(
                select(ExternalCorpusLink).where(
                    ExternalCorpusLink.kb_id == kb_id,
                    ExternalCorpusLink.ext_corpus_id == ext_corpus_id,
                )
            ).first()
            if dup is not None:
                raise ValueError("该外部语料库已挂接到本知识库")
            link = ExternalCorpusLink(
                link_id=new_id("extlink"),
                kb_id=kb_id,
                ext_corpus_id=ext_corpus_id,
                alias=alias,
            )
            s.add(link)
            s.commit()
            s.refresh(link)
            return link

    def update_external_link(
        self, kb_id: str, link_id: str, alias: str | None = None
    ) -> ExternalCorpusLink | None:
        link = self.get_external_link(kb_id, link_id)
        if link is None:
            return None
        if alias is not None:
            alias = alias.strip()
            if not alias or len(alias) > 40:
                raise ValueError("别名不能为空且不超过 40 字")
            if alias != link.alias:
                conflict = self.alias_conflict(alias)
                if conflict is not None:
                    raise AliasConflictError(conflict)
                link.alias = alias
        with Session(self.engine) as s:
            s.add(link)
            s.commit()
            s.refresh(link)
        return link

    def remove_external_link(self, kb_id: str, link_id: str) -> bool:
        link = self.get_external_link(kb_id, link_id)
        if link is None:
            return False
        with Session(self.engine) as s:
            s.delete(s.get(ExternalCorpusLink, link.link_id))
            s.commit()
        return True

    # ---------- 模型连接 ----------
    def create_connection(self, data) -> ModelConnection:
        conn = ModelConnection(
            conn_id=new_id("conn"),
            name=data.name,
            provider_type=data.provider_type,
            base_url=data.base_url,
            api_key=data.api_key,
            available_models=list(data.available_models),
            embedding_model=(data.embedding_model or "").strip() or None,
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
                if key == "embedding_model":
                    value = value.strip() or None
                setattr(conn, key, value)
            s.add(conn)
            s.commit()
            s.refresh(conn)
            return conn

    def connection_in_use(self, conn_id: str) -> bool:
        with Session(self.engine) as s:
            return (
                s.exec(select(Agent).where(Agent.model_connection_id == conn_id)).first()
                is not None
            )

    def delete_connection(self, conn_id: str) -> bool:
        if self.get_connection(conn_id) is None:
            return False
        if self.connection_in_use(conn_id):
            raise InUseError("该连接被智能体引用，无法删除")
        with Session(self.engine) as s:
            s.delete(s.get(ModelConnection, conn_id))
            s.commit()
        return True

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

    def kb_in_use(self, kb_id: str) -> bool:
        with Session(self.engine) as s:
            return (
                s.exec(
                    select(AgentKnowledgeBase).where(AgentKnowledgeBase.kb_id == kb_id)
                ).first()
                is not None
            )

    def delete_knowledge_base(self, kb_id: str) -> bool:
        if self.get_knowledge_base(kb_id) is None:
            return False
        if self.kb_in_use(kb_id):
            raise InUseError("该知识库已被智能体绑定，无法删除")
        with Session(self.engine) as s:
            docs = s.exec(select(Document).where(Document.kb_id == kb_id)).all()
            all_chunk_ids: list[str] = []
            for d in docs:
                chunks = s.exec(select(Chunk).where(Chunk.doc_id == d.doc_id)).all()
                all_chunk_ids.extend(c.chunk_id for c in chunks)
                for c in chunks:
                    s.delete(c)
                s.delete(d)
            s.delete(s.get(KnowledgeBase, kb_id))
            s.commit()
        if self.vs is not None and all_chunk_ids:
            try:
                self.vs.delete(kb_id, all_chunk_ids)
            except Exception:
                pass
        return True

    def ingest_document(
        self, kb_id: str, file_name: str, format: str, text: str, size: int = 0
    ) -> Document:
        if self.get_knowledge_base(kb_id) is None:
            raise ValueError("knowledge base not found")
        content = (text or "").strip()
        if not content:
            raise ValueError("empty document")
        if not size:
            size = len((text or "").encode("utf-8"))
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

    def get_document(self, doc_id: str) -> Document | None:
        with Session(self.engine) as s:
            return s.get(Document, doc_id)

    def delete_document(self, doc_id: str) -> bool:
        with Session(self.engine) as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                return False
            chunks = s.exec(select(Chunk).where(Chunk.doc_id == doc_id)).all()
            ids = [c.chunk_id for c in chunks]
            kb_id = doc.kb_id
            for c in chunks:
                s.delete(c)
            s.delete(doc)
            s.commit()
        if self.vs is not None and ids:
            try:
                self.vs.delete(kb_id, ids)
            except Exception:
                pass
        return True

    # ---------- 智能体 ----------
    def create_agent(
        self,
        name,
        model_connection_id,
        model_name,
        kb_ids,
        persona,
        expertise,
        speaking_style=None,
        speech_pace=None,
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
        if not (persona or "").strip():
            raise ValueError("persona required")
        agent = Agent(
            agent_id=new_id("agent"),
            name=name,
            model_connection_id=model_connection_id,
            model_name=model_name,
            persona=persona,
            expertise=expertise,
            speaking_style=speaking_style,
            speech_pace=speech_pace or SpeechPace.BALANCED,
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

    def rooms_using(self, agent_id: str) -> int:
        """该智能体被多少个房间选用（含已结束房间——选用关系保留）。"""
        with Session(self.engine) as s:
            return len(
                s.exec(select(RoomAgent).where(RoomAgent.agent_id == agent_id)).all()
            )

    def agent_in_use(self, agent_id: str) -> bool:
        return self.rooms_using(agent_id) > 0

    def delete_agent(self, agent_id: str) -> bool:
        if self.get_agent(agent_id) is None:
            return False
        if self.agent_in_use(agent_id):
            raise InUseError("该智能体已被房间选用，无法删除")
        with Session(self.engine) as s:
            for row in s.exec(
                select(AgentKnowledgeBase).where(AgentKnowledgeBase.agent_id == agent_id)
            ).all():
                s.delete(row)
            s.delete(s.get(Agent, agent_id))
            s.commit()
        return True

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
