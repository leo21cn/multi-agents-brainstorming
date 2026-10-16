"""一次性迁移：为 human_member 增加 token 列。

在 program/ 下运行：
  python scripts/migrate_member_token.py
迁移幂等：列已存在则跳过。既有行留空——令牌只能由进房或认领发放，回填一个没人知道
的随机串只会让「有令牌」与「能证明身份」脱钩，故既有房间的主持人须凭管理令牌认领。
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

    if "token" in _columns(cur, "human_member"):
        conn.close()
        return "already migrated; skipped"

    cur.execute("ALTER TABLE human_member ADD COLUMN token TEXT")
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM human_member").fetchone()[0]
    cols = _columns(cur, "human_member")
    conn.close()
    return f"migrated: {n} members left without a token; cols={cols}"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
