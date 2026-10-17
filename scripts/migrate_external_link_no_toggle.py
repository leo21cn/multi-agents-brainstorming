"""一次性迁移：external_corpus_link 去掉 enabled 列（取消启停功能，只保留挂接与解除）。

在 program/ 下运行：
  python scripts/migrate_external_link_no_toggle.py
迁移幂等：列已不存在则跳过。挂接即生效，解除即回到纯本地。
"""

import sqlite3
import sys
from pathlib import Path

_PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAM))
sys.path.insert(0, str(_PROGRAM / "backend"))

from app.settings import load_settings  # noqa: E402

_NEW_DDL = """
CREATE TABLE IF NOT EXISTS external_corpus_link_new (
  link_id TEXT PRIMARY KEY,
  kb_id TEXT NOT NULL,
  ext_corpus_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(alias),
  UNIQUE(kb_id, ext_corpus_id)
)
"""


def _columns(cur, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def migrate(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_corpus_link'"
    )
    if cur.fetchone() is None:
        conn.close()
        return "no external_corpus_link table; nothing to do"
    if "enabled" not in _columns(cur, "external_corpus_link"):
        conn.close()
        return "already migrated; skipped"
    cur.execute(_NEW_DDL)
    cur.execute(
        "INSERT INTO external_corpus_link_new (link_id, kb_id, ext_corpus_id, alias, created_at) "
        "SELECT link_id, kb_id, ext_corpus_id, alias, created_at FROM external_corpus_link"
    )
    cur.execute("DROP TABLE external_corpus_link")
    cur.execute("ALTER TABLE external_corpus_link_new RENAME TO external_corpus_link")
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM external_corpus_link").fetchone()[0]
    conn.close()
    return f"migrated: enabled column dropped; {n} links kept"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
