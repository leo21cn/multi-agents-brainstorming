from sqlmodel import Session, select

from app.domain.models import Chunk
from app.rag import embeddings


def test_upload_creates_document_and_chunks(client, env, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [[0.1] * 8 for _ in texts])
    kb = client.post(
        "/knowledge-bases", json={"name": "技术架构", "dimension": "技术架构"}
    ).json()
    files = {"file": ("doc.md", "十二要素应用。技术债治理。适配器与端口。", "text/markdown")}
    r = client.post("/documents", data={"kb_id": kb["kb_id"]}, files=files)
    assert r.status_code == 201
    assert r.json()["status"] == "已入库"
    with Session(env.engine) as s:
        assert len(s.exec(select(Chunk)).all()) > 0


def test_upload_rejects_unsupported_format(client):
    kb = client.post(
        "/knowledge-bases", json={"name": "x", "dimension": "技术架构"}
    ).json()
    r = client.post(
        "/documents",
        data={"kb_id": kb["kb_id"]},
        files={"file": ("a.pdf", "x", "application/pdf")},
    )
    assert r.status_code == 422
