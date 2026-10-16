import json
import sqlite3

from scripts.migrate_multi_addressee import migrate

LEGACY_DDL = """
CREATE TABLE room (room_id VARCHAR NOT NULL PRIMARY KEY, proposition VARCHAR NOT NULL)
"""

MESSAGE_DDL = """
CREATE TABLE message (
	message_id VARCHAR NOT NULL,
	room_id VARCHAR NOT NULL,
	source_type VARCHAR NOT NULL,
	source_id VARCHAR NOT NULL,
	content VARCHAR NOT NULL,
	addressee_agent_id VARCHAR,
	cited_kb_ids JSON NOT NULL,
	no_kb_citation BOOL NOT NULL,
	truncated BOOL NOT NULL,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (message_id),
	FOREIGN KEY(room_id) REFERENCES room (room_id)
)
"""


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_DDL)
    conn.execute(MESSAGE_DDL)
    conn.execute("CREATE INDEX ix_message_room_id ON message (room_id)")
    conn.execute("INSERT INTO room VALUES ('r1', '命题')")
    conn.execute(
        "INSERT INTO message VALUES"
        " ('m1','r1','人类','mem_1','问甲','agent_a','[]',0,0,'2026-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO message VALUES"
        " ('m2','r1','人类','mem_1','群发',NULL,'[]',0,0,'2026-01-02 00:00:00')"
    )
    conn.commit()
    conn.close()


def test_migrate_replaces_single_addressee_with_json_list(tmp_path):
    db = str(tmp_path / "mig.db")
    _make_db(db)

    out = migrate(db)

    assert out.startswith("migrated:")
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(message)")]
    rows = dict(conn.execute("SELECT message_id, addressee_agent_ids FROM message").fetchall())
    conn.close()
    assert "addressee_agent_ids" in cols
    assert "addressee_agent_id" not in cols
    assert json.loads(rows["m1"]) == ["agent_a"]
    assert json.loads(rows["m2"]) == []


def test_migrated_schema_matches_fresh_schema(tmp_path):
    """迁移后表结构须与新建库一致，否则线上库与测试库长期跑在两套模式上。"""
    from app.domain.models import Message
    from app.persistence.db import get_engine, init_db
    from app.settings import Settings

    db = str(tmp_path / "mig.db")
    _make_db(db)
    migrate(db)
    fresh_path = str(tmp_path / "fresh.db")
    init_db(get_engine(Settings("", fresh_path, str(tmp_path), 6000, 8000)))

    def shape(path):
        conn = sqlite3.connect(path)
        rows = conn.execute("PRAGMA table_info(message)").fetchall()
        conn.close()
        return [(r[1], r[2]) for r in rows]

    assert shape(db) == shape(fresh_path)
    assert [c.name for c in Message.__table__.columns] == [c for c, _ in shape(db)]


def test_migrate_keeps_index_and_referential_integrity(tmp_path):
    """重建表会连带删掉索引；漏建会让房间消息列表退化为全表扫描。"""
    db = str(tmp_path / "mig.db")
    _make_db(db)

    migrate(db)

    conn = sqlite3.connect(db)
    indexes = [r[1] for r in conn.execute("PRAGMA index_list(message)")]
    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    count = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
    conn.close()
    assert "ix_message_room_id" in indexes
    assert fk_violations == []
    assert count == 2


def test_migrate_is_idempotent(tmp_path):
    db = str(tmp_path / "mig.db")
    _make_db(db)
    migrate(db)

    assert migrate(db) == "already migrated; skipped"


def test_migrate_without_message_table_is_noop(tmp_path):
    db = str(tmp_path / "mig.db")
    sqlite3.connect(db).close()

    assert migrate(db) == "no message table; nothing to do"
