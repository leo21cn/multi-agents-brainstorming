"""一次性迁移：为 message 增加 addressee_member_ids 列。

在 program/ 下运行：
  python scripts/migrate_addressee_member.py
迁移幂等：列已存在则跳过。既有行取 NULL，读出时按空列表处理——历史消息没有指向人类
成员的受话者，语义不变。与 addressee_agent_ids 并存而不合并：智能体列的名字与校验口径
都锚在阵容内智能体，还决定串行应答次序；成员列只决定发言权授予与界面标注。
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

    if "addressee_member_ids" in _columns(cur, "message"):
        conn.close()
        return "already migrated; skipped"

    cur.execute("ALTER TABLE message ADD COLUMN addressee_member_ids JSON")
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM message").fetchone()[0]
    cols = _columns(cur, "message")
    conn.close()
    return f"migrated: {n} messages gained an empty member addressee list; cols={cols}"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
