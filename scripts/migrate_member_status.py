"""一次性迁移：为 human_member 增加 status 列（成员在线状态：在线／已退出）。

在 program/ 下运行：
  python scripts/migrate_member_status.py
迁移幂等：列已存在则跳过。既有行一律回填 ONLINE（成员名入库，显示值为「在线」）——
既有成员默认在线，退出口径只对迁移后发生的退出生效。
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
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='human_member'")
    if cur.fetchone() is None:
        conn.close()
        return "no human_member table; nothing to do"

    if "status" in _columns(cur, "human_member"):
        conn.close()
        return "already migrated; skipped"

    cur.execute("ALTER TABLE human_member ADD COLUMN status TEXT NOT NULL DEFAULT 'ONLINE'")
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM human_member").fetchone()[0]
    cols = _columns(cur, "human_member")
    conn.close()
    return f"migrated: {n} members defaulted to ONLINE; cols={cols}"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
