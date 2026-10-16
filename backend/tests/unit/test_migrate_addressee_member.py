import sqlite3

from scripts.migrate_addressee_member import migrate


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE message (message_id TEXT PRIMARY KEY, room_id TEXT, content TEXT)"
    )
    conn.execute("INSERT INTO message VALUES ('m1','r1','甲')")
    conn.commit()
    conn.close()


def test_migrate_adds_the_member_addressee_column(tmp_path):
    db = str(tmp_path / "mig.db")
    _make_db(db)

    out = migrate(db)

    assert out.startswith("migrated:")
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(message)")]
    assert "addressee_member_ids" in cols
    conn.close()


def test_migrate_is_idempotent(tmp_path):
    db = str(tmp_path / "mig.db")
    _make_db(db)
    migrate(db)

    assert migrate(db) == "already migrated; skipped"


def test_migrate_without_message_table_is_noop(tmp_path):
    db = str(tmp_path / "mig.db")
    sqlite3.connect(db).close()

    assert migrate(db) == "no message table; nothing to do"
