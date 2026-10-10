"""把 docs/testdata 的三维度语料入知识库（供演示与测试）。"""

import sys
from pathlib import Path

from app.domain.enums import KnowledgeDimension
from app.persistence.db import get_engine, init_db
from app.rag.splitter import split_text
from app.rag.vectorstore import default_vector_store
from app.services.config_service import ConfigService
from app.settings import Settings

DIMENSIONS = {"业务领域", "项目管理与软件工程方法论", "技术架构"}
DIR_TO_DIMENSION = {
    "business": KnowledgeDimension.BUSINESS,
    "methodology": KnowledgeDimension.METHODOLOGY,
    "architecture": KnowledgeDimension.ARCHITECTURE,
}


def find_testdata() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        cand = parent / "docs" / "testdata"
        if cand.is_dir():
            return cand
    raise FileNotFoundError("docs/testdata not found")


def ingest(testdata_dir: Path, db_path, data_dir) -> dict:
    engine = get_engine(Settings("", str(db_path), str(data_dir), 6000, 8000))
    init_db(engine)
    vs = default_vector_store(str(data_dir))
    svc = ConfigService(engine, vs)
    _configure_embeddings(svc)
    counts: dict[str, int] = {}
    for sub, dimension in DIR_TO_DIMENSION.items():
        kb = svc.get_or_create_knowledge_base(dimension.value, dimension)
        chunks_total = 0
        for f in sorted((Path(testdata_dir) / sub).glob("*.md")):
            text = f.read_text(encoding="utf-8")
            chunks_total += len(split_text(text))
            svc.ingest_document(kb.kb_id, f.name, "md", text)
        counts[dimension.value] = chunks_total
    return counts


def _configure_embeddings(svc: ConfigService) -> None:
    """按已配置的模型连接使用真实嵌入；无连接或空密钥时回退到离线哈希嵌入。"""
    from app.rag import embeddings

    conns = svc.list_connections()
    if conns and conns[0].api_key:
        from app.models.openai_compat import OpenAICompatProvider

        embeddings.configure(OpenAICompatProvider(conns[0].base_url, conns[0].api_key))


def main() -> None:
    settings = Settings("", "./data/sparkwave.db", "./data", 6000, 8000)
    counts = ingest(find_testdata(), settings.db_path, settings.data_dir)
    print("ingested chunks per dimension:", counts)


if __name__ == "__main__":
    sys.exit(main())
