"""一次性迁移：为 agent 增加 speech_pace 列、为 message 增加 truncated 列，并把种子占位提示词改写为头脑风暴口径。

在 program/ 下运行：
  python scripts/migrate_speech_pace.py
迁移幂等：两列均已存在则整体跳过。提示词只替换可精确识别的种子值——形如「X的角色人设」
「X的专长」的占位串，以及与改写前原文逐字相等的发言风格；人工编辑过的值一律不动。
"""

import sqlite3
import sys
from pathlib import Path

_PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROGRAM))
sys.path.insert(0, str(_PROGRAM / "backend"))

from app.settings import load_settings  # noqa: E402
from scripts.agent_presets import (  # noqa: E402
    AGENT_PRESETS,
    LEGACY_SPEAKING_STYLES,
    is_placeholder_expertise,
    is_placeholder_persona,
)


def _columns(cur, table: str) -> list[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def migrate(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent'")
    if cur.fetchone() is None:
        conn.close()
        return "no agent table; nothing to do"

    agent_cols = _columns(cur, "agent")
    has_message = (
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message'").fetchone()
        is not None
    )
    message_cols = _columns(cur, "message") if has_message else []
    if "speech_pace" in agent_cols and (not has_message or "truncated" in message_cols):
        conn.close()
        return "already migrated; skipped"

    if "speech_pace" not in agent_cols:
        # 枚举入库为名字，缺省值必须写 BALANCED 而非「均衡」
        cur.execute(
            "ALTER TABLE agent ADD COLUMN speech_pace VARCHAR NOT NULL DEFAULT 'BALANCED'"
        )
    if has_message and "truncated" not in message_cols:
        cur.execute("ALTER TABLE message ADD COLUMN truncated BOOLEAN NOT NULL DEFAULT 0")

    touched = {"persona": 0, "expertise": 0, "speaking_style": 0, "speech_pace": 0}
    # 先 fetchall 再在循环里 UPDATE：复用同一 cursor 边迭代边写会让迭代失效
    rows = cur.execute(
        "SELECT agent_id, name, persona, expertise, speaking_style FROM agent"
    ).fetchall()
    for agent_id, name, persona, expertise, style in rows:
        preset = AGENT_PRESETS.get(name)
        if preset is None:
            continue
        new_persona, new_expertise, new_style, pace = preset
        sets: list[str] = []
        vals: list = []
        if is_placeholder_persona(name, persona):
            sets.append("persona=?")
            vals.append(new_persona)
            touched["persona"] += 1
        if is_placeholder_expertise(name, expertise):
            sets.append("expertise=?")
            vals.append(new_expertise)
            touched["expertise"] += 1
        if style is not None and style == LEGACY_SPEAKING_STYLES.get(name):
            sets.append("speaking_style=?")
            vals.append(new_style)
            touched["speaking_style"] += 1
        sets.append("speech_pace=?")
        vals.append(pace.name)
        touched["speech_pace"] += 1
        vals.append(agent_id)
        cur.execute(f"UPDATE agent SET {', '.join(sets)} WHERE agent_id=?", vals)

    conn.commit()
    n_msgs = cur.execute("SELECT COUNT(*) FROM message").fetchone()[0] if has_message else 0
    # 列清单要在 close 之前取：重开连接不关会在 Windows 上留下文件句柄
    agent_cols = _columns(cur, "agent")
    message_cols = _columns(cur, "message") if has_message else []
    conn.close()
    return (
        f"migrated: agents touched={touched}; {n_msgs} messages set truncated=0; "
        f"agent cols={agent_cols}; message cols={message_cols}"
    )


def main() -> None:
    db_path = load_settings().db_path
    print("db:", db_path)
    print(migrate(db_path))


if __name__ == "__main__":
    main()
