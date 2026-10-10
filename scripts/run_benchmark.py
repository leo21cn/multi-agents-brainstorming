"""端到端基准跑通：种子语料与配置 → 建房 → 讨论 → 结论 → 导出，并输出指标。"""

import os
import sys
import tempfile
from pathlib import Path

import yaml

from app.memory.service import MemoryService
from app.models.demo_provider import DemoProvider
from app.persistence.db import get_engine, init_db
from app.rag.embeddings import EmbeddingAdapter
from app.rag.retrieval import RetrievalService
from app.rag.vectorstore import InMemoryVectorStore
from app.services.conclusion_service import ConclusionService
from app.services.config_service import ConfigService
from app.services.room_service import RoomService
from app.services.speech_service import SpeechService
from app.settings import Settings
from scripts.seed_config import seed
from scripts.seed_corpus import find_testdata, ingest

ROSTER = ["架构师", "交付负责人", "业务领域专家", "成本／风险分析师"]


def run(propositions_path: str, work_dir: str | None = None) -> dict:
    work = Path(work_dir or tempfile.mkdtemp())
    data = work / "data"
    data.mkdir(parents=True, exist_ok=True)
    db = work / "bench.db"

    ingest(find_testdata(), db, data)
    seed(db, data)

    engine = get_engine(Settings("", str(db), str(data), 6000, 8000))
    init_db(engine)
    vs = InMemoryVectorStore(str(data / "vectors.json"))
    config = ConfigService(engine, vs)
    room = RoomService(engine, config)
    memory = MemoryService(engine, 6000, 8000)
    speech = SpeechService(
        engine, config, memory, RetrievalService(vs, EmbeddingAdapter()), lambda a: DemoProvider()
    )
    conclusion = ConclusionService(engine, config, memory, str(data))

    props = yaml.safe_load(Path(propositions_path).read_text(encoding="utf-8"))["propositions"]
    agents = {a.name: a for a in config.list_agents()}
    roster = [agents[n] for n in ROSTER if n in agents]

    consistency: list[float] = []
    annotation_ok = True
    unauthorized = 0
    export_ok = True

    for p in props:
        room_obj = room.create_room(p["text"], "人类主持控制", [a.agent_id for a in roster], "陈默")
        before = len(memory.all_messages(room_obj.room_id))
        for agent in roster:
            speech.generate(room_obj.room_id, agent.agent_id, intent=p["text"])
        after = memory.all_messages(room_obj.room_id)
        if len(after) - before != len(roster):
            unauthorized += 1
        for msg in after:
            if msg.source_type.value == "智能体":
                bound = set(config.bound_kb_ids(msg.source_id))
                consistency.append(1.0 if set(msg.cited_kb_ids) <= bound else 0.0)

        # 举手 → 放行
        room_obj2 = room_obj
        doc = conclusion.generate(room_obj2.room_id)
        for item in doc.sections["关键观点"] + doc.sections["分歧点"]:
            src = item.get("source", {})
            if not (src.get("agent") and src.get("kbs")):
                annotation_ok = False
        exp = conclusion.export_transcript(room_obj2.room_id)
        if exp.entry_count != len(memory.all_messages(room_obj2.room_id)):
            export_ok = False

    rate = sum(consistency) / len(consistency) if consistency else 0.0
    return {
        "propositions": len(props),
        "source_consistency_rate": round(rate, 3),
        "annotation_completeness": 1.0 if annotation_ok else 0.0,
        "unauthorized_speech": unauthorized,
        "export_count_match": export_ok,
        "model": "dashscope" if os.environ.get("DASHSCOPE_API_KEY") else "demo",
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "scripts/benchmark_propositions.yaml"
    report = run(path)
    for key, value in report.items():
        print(f"{key}: {value}")
    ok = (
        report["source_consistency_rate"] >= 0.9
        and report["annotation_completeness"] == 1.0
        and report["unauthorized_speech"] == 0
        and report["export_count_match"] is True
    )
    sys.exit(0 if ok else 1)
