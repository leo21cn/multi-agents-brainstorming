"""一次性迁移：新建 external_corpus_link 表（外部知识库语料库挂接）。

在 program/ 下运行：
  python scripts/migrate_external_link.py
迁移幂等：表已存在则跳过。空库（knowledge_base 尚未建）同样只建本表，无数据回填。

约束口径：
  - alias 全局唯一（跨知识库不重名）；
  - (kb_id, ext_corpus_id) 唯一（同一知识库不重复挂接同一外部语料库）；
  - 外部语料库被删/归档时本表行保留，检索跳过并显式标注。
"""

import sqlite3
import sys
from pathlib import Path

_PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAM))
sys.path.insert(0, str(_PROGRAM / "backend"))

from app.settings import load_settings  # noqa: E402

_DDL = """
CREATE TABLE IF NOT EXISTS external_corpus_link (
  link_id TEXT PRIMARY KEY,
  kb_id TEXT NOT NULL,
  ext_corpus_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  UNIQUE(alias),
  UNIQUE(kb_id, ext_corpus_id)
)
"""


def migrate(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_corpus_link'"
    )
    if cur.fetchone() is not None:
        conn.close()
        return "already migrated; skipped"
    cur.execute(_DDL)
    conn.commit()
    conn.close()
    return "migrated: external_corpus_link created"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
