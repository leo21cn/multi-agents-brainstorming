import sqlite3

from scripts.migrate_speech_pace import migrate

LEGACY_AGENT_DDL = """
CREATE TABLE agent (
    agent_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
    model_connection_id VARCHAR NOT NULL, model_name VARCHAR NOT NULL,
    created_at DATETIME, updated_at DATETIME,
    persona VARCHAR, expertise VARCHAR, speaking_style VARCHAR
)
"""

LEGACY_MESSAGE_DDL = """
CREATE TABLE message (
    message_id VARCHAR PRIMARY KEY, room_id VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL, source_id VARCHAR NOT NULL,
    content VARCHAR NOT NULL, cited_kb_ids JSON, no_kb_citation BOOLEAN NOT NULL,
    created_at DATETIME, addressee_agent_id VARCHAR
)
"""


def _legacy_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(LEGACY_AGENT_DDL)
    conn.execute(LEGACY_MESSAGE_DDL)
    conn.executemany(
        "INSERT INTO agent (agent_id,name,model_connection_id,model_name,persona,expertise,speaking_style)"
        " VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.execute(
        "INSERT INTO message (message_id,room_id,source_type,source_id,content,no_kb_citation)"
        " VALUES ('m1','r1','AGENT','a1','正文',0)"
    )
    conn.commit()
    conn.close()


def _cols(path, table):
    conn = sqlite3.connect(str(path))
    rows = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    conn.close()
    return rows


def _agent(path, agent_id):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM agent WHERE agent_id=?", (agent_id,)).fetchone())
    conn.close()
    return row


def test_no_agent_table_is_a_noop(tmp_path):
    db = tmp_path / "mig.db"
    sqlite3.connect(str(db)).close()
    assert migrate(str(db)) == "no agent table; nothing to do"


def test_adds_both_columns_and_backfills(tmp_path):
    db = tmp_path / "mig.db"
    _legacy_db(db, [
        ("a1", "批判者", "c1", "qwen-max", "批判者的角色人设", "批判者的专长",
         "反例驱动，先质疑共识与隐含假设、挑出漏洞，再提建设性替代方案。"),
    ])

    out = migrate(str(db))

    assert out.startswith("migrated:")
    assert "speech_pace" in _cols(db, "agent")
    assert "truncated" in _cols(db, "message")
    row = _agent(db, "a1")
    assert row["persona"] == "默认共识里有漏洞，职责是在投入前把它找出来。"
    assert row["expertise"] == "假设检验、反例构造、失败模式识别。"
    assert row["speaking_style"] == "直接质疑，给一个反例或会失败的场景；不铺垫。"
    # 枚举入库为名字而非值，写「快闪」会在读取时抛解析错误
    assert row["speech_pace"] == "FLASH"


def test_existing_message_rows_get_truncated_false(tmp_path):
    db = tmp_path / "mig.db"
    _legacy_db(db, [("a1", "架构师", "c1", "qwen-max", "架构师的角色人设", "架构师的专长", None)])

    migrate(str(db))

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT truncated FROM message WHERE message_id='m1'").fetchone()[0] == 0
    conn.close()


def test_edited_values_are_never_overwritten(tmp_path):
    db = tmp_path / "mig.db"
    _legacy_db(db, [
        ("a1", "架构师", "c1", "qwen-max", "我自己写的人设", "我自己写的专长", "我自己写的风格"),
    ])

    migrate(str(db))

    row = _agent(db, "a1")
    assert row["persona"] == "我自己写的人设"
    assert row["expertise"] == "我自己写的专长"
    assert row["speaking_style"] == "我自己写的风格"


def test_unknown_role_name_keeps_balanced(tmp_path):
    db = tmp_path / "mig.db"
    _legacy_db(db, [("a1", "外部顾问", "c1", "qwen-max", "外部顾问的角色人设", "外部顾问的专长", None)])

    migrate(str(db))

    assert _agent(db, "a1")["speech_pace"] == "BALANCED"
    assert _agent(db, "a1")["persona"] == "外部顾问的角色人设"


def test_second_run_is_skipped(tmp_path):
    db = tmp_path / "mig.db"
    _legacy_db(db, [("a1", "架构师", "c1", "qwen-max", "架构师的角色人设", "架构师的专长", None)])

    migrate(str(db))

    assert migrate(str(db)) == "already migrated; skipped"
