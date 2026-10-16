"""一次性迁移：message 的受话者由单值改为 JSON 列表。

在 program/ 下运行：
  python scripts/migrate_multi_addressee.py
迁移幂等：旧列已不在则跳过。既有行按「有值转单元素列表、NULL 转空列表」回填，
群发与单发语义不变。

SQLite 不能直接 DROP 处于外键定义中的列，故走「建新表-拷贝-改名-重建索引」；新表 DDL
由 SQLModel 元数据编译得出，令迁移后的库与新建库模式一致，而不是就地拼一份近似 DDL。
"""

import json
import sqlite3
import sys
from pathlib import Path

_PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAM))
sys.path.insert(0, str(_PROGRAM / "backend"))

from sqlalchemy.dialects import sqlite as sqlite_dialect  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.domain.models import Message  # noqa: E402
from app.settings import load_settings  # noqa: E402


def _columns(cur, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _create_ddl(table, name: str) -> str:
    ddl = str(CreateTable(table).compile(dialect=sqlite_dialect.dialect()))
    return ddl.replace(f"CREATE TABLE {table.name}", f"CREATE TABLE {name}", 1)


def migrate(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message'")
    if cur.fetchone() is None:
        conn.close()
        return "no message table; nothing to do"

    if "addressee_agent_id" not in _columns(cur, "message"):
        conn.close()
        return "already migrated; skipped"

    cur.execute("PRAGMA foreign_keys=OFF")
    cur.execute("BEGIN")
    cur.execute("DROP TABLE IF EXISTS message_new")
    cur.execute(_create_ddl(Message.__table__, "message_new"))

    # 只搬旧表里确实存在的列：本迁移面向历史库，此后模型再加列也不该让它跑不动
    present = set(_columns(cur, "message"))
    keep = [
        c.name
        for c in Message.__table__.columns
        if c.name != "addressee_agent_ids" and c.name in present
    ]
    collist = ", ".join(f'"{c}"' for c in keep)
    cur.execute(f'INSERT INTO "message_new" ({collist}) SELECT {collist} FROM "message"')

    cur.execute("SELECT message_id, addressee_agent_id FROM message")
    rows = cur.fetchall()
    converted = sum(1 for _mid, old in rows if old)
    for message_id, old in rows:
        cur.execute(
            "UPDATE message_new SET addressee_agent_ids = ? WHERE message_id = ?",
            (json.dumps([old] if old else [], ensure_ascii=False), message_id),
        )

    cur.execute('DROP TABLE "message"')
    cur.execute('ALTER TABLE "message_new" RENAME TO "message"')
    for index in Message.__table__.indexes:
        cur.execute(str(CreateIndex(index).compile(dialect=sqlite_dialect.dialect())))
    cur.execute("COMMIT")
    cur.execute("PRAGMA foreign_keys=ON")

    violations = cur.execute("PRAGMA foreign_key_check").fetchall()
    cols = _columns(cur, "message")
    conn.close()
    if violations:
        return f"migrated but foreign_key_check reported {len(violations)} violations: {violations[:3]}"
    return f"migrated: {len(rows)} messages, {converted} directed; cols={cols}"


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
