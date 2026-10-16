import sqlite3

from scripts.migrate_member_token import migrate


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE human_member (member_id TEXT PRIMARY KEY, room_id TEXT, display_name TEXT)"
    )
    conn.execute("INSERT INTO human_member VALUES ('mem_1','room_1','陈')")
    conn.commit()
    conn.close()


def test_migrate_adds_token_column_and_leaves_existing_rows_empty(tmp_path):
    """既有行不回填随机串：令牌只能由进房或认领发放，回填一个没人知道的值只会让
    「有令牌」与「能证明身份」脱钩。"""
    db = str(tmp_path / "mig.db")
    _make_db(db)

    out = migrate(db)

    assert out.startswith("migrated:")
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(human_member)")]
    assert "token" in cols
    assert conn.execute("SELECT token FROM human_member").fetchone()[0] is None
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
