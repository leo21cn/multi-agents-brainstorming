"""一次性迁移：把角色提示词卡三字段并入智能体，删除 role_card 表。

在 program/ 下运行：
  python scripts/migrate_merge_rolecard.py
迁移幂等：已完成（agent 无 role_card_id 且无 role_card 表）则跳过。
SQLite 不允许直接 DROP 处于外键定义中的列，故用标准的建表-拷贝-改名重建法。
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


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def migrate(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # 手动事务，确保 PRAGMA foreign_keys 生效
    cur = conn.cursor()

    if not _table_exists(cur, "agent"):
        conn.close()
        return "no agent table; nothing to do"

    cols = _columns(cur, "agent")
    has_card = _table_exists(cur, "role_card")
    if "persona" in cols and "role_card_id" not in cols and not has_card:
        conn.close()
        return "already migrated; skipped"

    cur.execute("PRAGMA foreign_keys=OFF")
    cur.execute("BEGIN")

    if "persona" not in cols:
        cur.execute("ALTER TABLE agent ADD COLUMN persona TEXT")
        cur.execute("ALTER TABLE agent ADD COLUMN expertise TEXT")
        cur.execute("ALTER TABLE agent ADD COLUMN speaking_style TEXT")
        cols = _columns(cur, "agent")

    if has_card and "role_card_id" in cols:
        cur.execute(
            """
            UPDATE agent SET
              persona = COALESCE((SELECT persona FROM role_card WHERE card_id = agent.role_card_id), ''),
              expertise = COALESCE((SELECT expertise FROM role_card WHERE card_id = agent.role_card_id), ''),
              speaking_style = (SELECT speaking_style FROM role_card WHERE card_id = agent.role_card_id)
            WHERE persona IS NULL OR persona = ''
            """
        )
    else:
        cur.execute("UPDATE agent SET persona = COALESCE(persona, ''), expertise = COALESCE(expertise, '')")

    # 重建 agent 表，剔除 role_card_id（保留主键/非空/默认值；外键约束改由应用层保证）
    cur.execute("PRAGMA table_info(agent)")
    info = [r for r in cur.fetchall() if r[1] != "role_card_id"]
    defs = []
    for _cid, name, type_, notnull, dflt, pk in info:
        d = f'"{name}" {type_ or "TEXT"}'
        if pk:
            d += " PRIMARY KEY"
        else:
            if notnull:
                d += " NOT NULL"
            if dflt is not None:
                d += f" DEFAULT {dflt}"
        defs.append(d)
    collist = ", ".join(f'"{r[1]}"' for r in info)
    cur.execute("DROP TABLE IF EXISTS agent_new")
    cur.execute(f'CREATE TABLE "agent_new" ({", ".join(defs)})')
    cur.execute(f'INSERT INTO "agent_new" ({collist}) SELECT {collist} FROM "agent"')
    cur.execute('DROP TABLE "agent"')
    cur.execute('ALTER TABLE "agent_new" RENAME TO "agent"')

    if has_card:
        cur.execute("DROP TABLE role_card")

    cur.execute("COMMIT")
    cur.execute("PRAGMA foreign_keys=ON")

    cur.execute("SELECT COUNT(*) FROM agent")
    n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM agent WHERE persona IS NULL OR persona = ''")
    empty = cur.fetchone()[0]
    final_cols = _columns(cur, "agent")
    conn.close()
    return (
        f"migrated: {n} agents, empty persona={empty}; "
        f"cols={final_cols}; role_card dropped={not _table_exists(sqlite3.connect(db_path).cursor(), 'role_card')}"
    )


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
