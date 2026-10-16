"""一次性迁移：为 model_connection 增加 embedding_model 列。

在 program/ 下运行：
  python scripts/migrate_embedding_model.py
迁移幂等：列已存在则跳过。既有行取 NULL，运行期按提供方缺省嵌入模型解析。
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
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_connection'")
    if cur.fetchone() is None:
        conn.close()
        return "no model_connection table; nothing to do"

    cols = _columns(cur, "model_connection")
    if "embedding_model" in cols:
        conn.close()
        return "already migrated; skipped"

    cur.execute("ALTER TABLE model_connection ADD COLUMN embedding_model TEXT")
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM model_connection")
    n = cur.fetchone()[0]
    cols = _columns(cur, "model_connection")
    conn.close()
    return f"migrated: {n} connections set to NULL; cols={cols}"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
