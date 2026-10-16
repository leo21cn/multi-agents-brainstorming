FIXED = ["命题", "关键观点", "分歧点", "初步结论／建议"]
TAIL_KEYS = FIXED[3:]
BOILER = ("见关键观点与分歧点；需主持人确认后收敛。", "待指派。", "见分歧点。", "见分歧点与待定项。")


def _discuss_and_conclude(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(
        f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]}
    )
    client.post(f"/rooms/{room['room_id']}/messages", json={"content": "补充一个合规约束。"})
    return room, client.post(f"/rooms/{room['room_id']}/conclusion")


def test_conclusion_sections_and_annotations(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    client.post(
        f"/rooms/{room['room_id']}/speak", json={"agent_id": seeded["agent"]["agent_id"]}
    )
    client.post(f"/rooms/{room['room_id']}/messages", json={"content": "补充一个合规约束。"})
    r = client.post(f"/rooms/{room['room_id']}/conclusion")
    assert r.status_code == 201
    doc = r.json()
    keys = list(doc["sections"].keys())
    assert keys[:4] == FIXED
    assert len(keys) - 4 <= 10
    assert len(doc["source_annotations"]) >= 1
    for ann in doc["source_annotations"]:
        assert ann["agent"] and ann["kbs"]


def test_conclusion_empty_memory_rejected(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    assert client.post(f"/rooms/{room['room_id']}/conclusion").status_code == 422


def test_conclusion_synth_sections_not_boilerplate(client, seeded, make_room):
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 201
    sections = r.json()["sections"]
    tail = {k: v for k, v in sections.items() if k not in FIXED[:3]}
    assert TAIL_KEYS[0] in tail
    assert all(isinstance(v, str) and v.strip() for v in tail.values())
    assert not [v for v in tail.values() if v in BOILER]
    assert not [v for v in tail.values() if "见关键观点" in v or "见分歧点" in v]
    # 离线综合由提示词内的命题与要点派生：必写章围绕命题起笔
    assert tail[TAIL_KEYS[0]].startswith("围绕命题「")


def test_conclusion_extra_sections_derived_from_discussion(client, seeded, make_room):
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 201
    sections = r.json()["sections"]
    extras = {k: v for k, v in sections.items() if k not in FIXED[:3]}
    assert len(extras) == 3  # 必写章加离线派生的两章
    for title, body in list(extras.items())[1:]:
        assert title and len(title) <= 20 and title not in FIXED
        assert isinstance(body, str) and body.strip()


def test_conclusion_synthesis_call_failure_rejected(client, seeded, make_room, monkeypatch):
    from app.services import conclusion_service

    class Boom:
        def chat(self, *a, **kw):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(conclusion_service, "DemoProvider", lambda: Boom())
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 502
    assert "模型调用失败" in r.json()["detail"]


def test_conclusion_unparseable_reply_rejected(client, seeded, make_room, monkeypatch):
    from app.services import conclusion_service

    class Garbage:
        def chat(self, *a, **kw):
            return "好的，总结如下……（没有 JSON）"

    monkeypatch.setattr(conclusion_service, "DemoProvider", lambda: Garbage())
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 502
    assert "不是" in r.json()["detail"]


def test_conclusion_missing_section_rejected(client, seeded, make_room, monkeypatch):
    import json as _json

    from app.services import conclusion_service

    class Partial:
        def chat(self, *a, **kw):
            return _json.dumps({"上线前的验证": "甲"}, ensure_ascii=False)

    monkeypatch.setattr(conclusion_service, "DemoProvider", lambda: Partial())
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 502
    assert "初步结论／建议" in r.json()["detail"]


def test_conclusion_duplicate_titles_rejected(client, seeded, make_room, monkeypatch):
    import json as _json

    from app.services import conclusion_service

    class Dup:
        def chat(self, *a, **kw):
            return _json.dumps(
                {"初步结论／建议": "甲", "同题": "乙", "同题 ": "丙"}, ensure_ascii=False
            )

    monkeypatch.setattr(conclusion_service, "DemoProvider", lambda: Dup())
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 502
    assert "重复" in r.json()["detail"]


def test_conclusion_too_many_extra_sections_rejected(client, seeded, make_room, monkeypatch):
    import json as _json

    from app.services import conclusion_service

    class Many:
        def chat(self, *a, **kw):
            return _json.dumps(
                {"初步结论／建议": "甲", **{f"章{i}": "乙" for i in range(11)}},
                ensure_ascii=False,
            )

    monkeypatch.setattr(conclusion_service, "DemoProvider", lambda: Many())
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 502
    assert "上限" in r.json()["detail"]


def test_conclusion_zero_extra_sections_accepted(client, seeded, make_room, monkeypatch):
    import json as _json

    from app.services import conclusion_service

    class Only:
        def chat(self, *a, **kw):
            return _json.dumps({"初步结论／建议": "讨论尚薄，只给收敛方向。"}, ensure_ascii=False)

    monkeypatch.setattr(conclusion_service, "DemoProvider", lambda: Only())
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 201
    assert list(r.json()["sections"].keys()) == FIXED


def test_conclusion_model_setting_round_trip_and_validation(client, env, seeded):
    r = client.get("/system/conclusion-model")
    assert r.status_code == 200 and r.json() == {"conn_id": None, "model_name": None}
    conn = seeded["conn"]
    cid, model = conn["conn_id"], conn["available_models"][0]
    r = client.patch("/system/conclusion-model", json={"conn_id": cid, "model_name": model})
    assert r.status_code == 200 and r.json() == {"conn_id": cid, "model_name": model}
    assert env.config.get_conclusion_model() == (cid, model)
    bad = client.patch(
        "/system/conclusion-model", json={"conn_id": cid, "model_name": "no-such"}
    )
    assert bad.status_code == 422
    half = client.patch("/system/conclusion-model", json={"conn_id": cid, "model_name": None})
    assert half.status_code == 422
    gone = client.patch(
        "/system/conclusion-model", json={"conn_id": "conn_none", "model_name": model}
    )
    assert gone.status_code == 422
    clear = client.patch("/system/conclusion-model", json={"conn_id": None, "model_name": None})
    assert clear.status_code == 200 and clear.json()["conn_id"] is None


def test_conclusion_model_setting_requires_token(env):
    from fastapi.testclient import TestClient

    from app.main import app

    bare = TestClient(app)
    assert bare.get("/system/conclusion-model").status_code == 401
    body = {"conn_id": "x", "model_name": "y"}
    assert bare.patch("/system/conclusion-model", json=body).status_code == 401


def test_conclusion_uses_configured_model_then_falls_back_to_roster(
    client, seeded, make_room, env, monkeypatch
):
    import json as _json

    from app.services import conclusion_service

    seen = []

    class Recorder:
        def chat(self, model_name, messages, *a, **kw):
            seen.append(model_name)
            return _json.dumps(
                {"初步结论／建议": "综合自记录。", "上线前的验证": "综合自记录。"},
                ensure_ascii=False,
            )

    monkeypatch.setattr(conclusion_service, "DemoProvider", lambda: Recorder())
    conn = env.config.list_connections()[0]
    model = conn.available_models[0]
    client.patch(
        "/system/conclusion-model", json={"conn_id": conn.conn_id, "model_name": model}
    )
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 201 and seen[-1] == model

    client.patch("/system/conclusion-model", json={"conn_id": None, "model_name": None})
    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 201 and seen[-1] == seeded["agent"]["model_name"]


def test_conclusion_markdown_annotation_shows_agent_only(client, seeded, make_room, env):
    from pathlib import Path

    _, r = _discuss_and_conclude(client, seeded, make_room)
    assert r.status_code == 201
    doc = r.json()
    text = (Path(env.data_dir) / doc["file_ref"]).read_text(encoding="utf-8")
    # 来源标注只呈现智能体名；知识库保留在来源标注数据里，不写进 Markdown
    assert "（架构师）" in text
    assert "（架构师 · " not in text
