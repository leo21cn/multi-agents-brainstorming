import sqlite3

from scripts.migrate_member_status import migrate


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE human_member (member_id TEXT PRIMARY KEY, room_id TEXT, display_name TEXT)"
    )
    conn.execute("INSERT INTO human_member VALUES ('mem_1','room_1','陈')")
    conn.commit()
    conn.close()


def test_migrate_adds_status_column_defaulting_to_online(tmp_path):
    db = str(tmp_path / "mig.db")
    _make_db(db)

    out = migrate(db)

    assert out.startswith("migrated:")
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(human_member)")]
    assert "status" in cols
    # 既有行一律回填 ONLINE（成员名入库）
    assert conn.execute("SELECT status FROM human_member").fetchone()[0] == "ONLINE"
    conn.close()


def test_migrate_is_idempotent(tmp_path):
    db = str(tmp_path / "mig.db")
    _make_db(db)
    migrate(db)

    assert migrate(db) == "already migrated; skipped"


def test_migrate_without_table_is_noop(tmp_path):
    db = str(tmp_path / "mig.db")
    sqlite3.connect(db).close()

    assert migrate(db) == "no human_member table; nothing to do"
