"""一次性迁移：为 message 增加 addressee_agent_id 列。

在 program/ 下运行：
  python scripts/migrate_addressee.py
迁移幂等：列已存在则跳过。既有行取 NULL，即群发消息，历史数据语义不变。
"""

import sqlite3
import sys
from pathlib import Path

_PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAM))
sys.path.insert(0, str(_PROGRAM / "backend"))

from app.settings import load_settings  # noqa: E402


def _columns(cur, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def migrate(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message'")
    if cur.fetchone() is None:
        conn.close()
        return "no message table; nothing to do"

    cols = _columns(cur, "message")
    if "addressee_agent_id" in cols:
        conn.close()
        return "already migrated; skipped"

    cur.execute("ALTER TABLE message ADD COLUMN addressee_agent_id TEXT")
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM message")
    n = cur.fetchone()[0]
    cols = _columns(cur, "message")
    conn.close()
    return f"migrated: {n} messages set to NULL; cols={cols}"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
