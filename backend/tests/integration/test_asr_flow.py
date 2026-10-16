"""语音转写：模型设置接口、REST 批量转写端点与 WS 流式语音事件。"""

from app.models.asr import DemoAsrProvider

STREAM_MODEL = "qwen-audio-3.0-asr-flash-streaming"


def test_asr_model_setting_round_trip_and_validation(client, seeded):
    # 缺省未配置
    assert client.get("/system/asr-model").json() == {"conn_id": None, "model_name": None}
    cid = seeded["conn"]["conn_id"]
    # 不成对 → 422
    assert (
        client.patch("/system/asr-model", json={"conn_id": cid, "model_name": None}).status_code
        == 422
    )
    assert (
        client.patch(
            "/system/asr-model", json={"conn_id": None, "model_name": STREAM_MODEL}
        ).status_code
        == 422
    )
    # 连接不存在 → 422
    assert (
        client.patch(
            "/system/asr-model", json={"conn_id": "nope", "model_name": STREAM_MODEL}
        ).status_code
        == 422
    )
    # 成对保存；转写模型名是自由文本，不校验可用对话模型列表
    r = client.patch("/system/asr-model", json={"conn_id": cid, "model_name": STREAM_MODEL})
    assert r.status_code == 200 and r.json()["model_name"] == STREAM_MODEL
    assert client.get("/system/asr-model").json() == {
        "conn_id": cid,
        "model_name": STREAM_MODEL,
    }
    # 清空恢复未配置
    assert (
        client.patch("/system/asr-model", json={"conn_id": None, "model_name": None}).status_code
        == 200
    )


def test_asr_requires_member_identity(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    r = client.raw.post(f"/rooms/{room['room_id']}/asr", content=b"\x00\x01")
    assert r.status_code == 401


def test_asr_unconfigured_rejected(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    r = client.post(f"/rooms/{room['room_id']}/asr", content=b"\x00\x01")
    assert r.status_code == 422
    assert r.json()["detail"] == "尚未配置语音转写模型，请先在管理后台设置"


def test_asr_empty_and_oversized_rejected(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.patch(
        "/system/asr-model",
        json={"conn_id": seeded["conn"]["conn_id"], "model_name": STREAM_MODEL},
    )
    r = client.post(f"/rooms/{rid}/asr", content=b"")
    assert r.status_code == 422 and r.json()["detail"] == "录音为空"
    r = client.post(f"/rooms/{rid}/asr", content=b"\x00" * 2_000_001)
    assert r.status_code == 422 and "录音过长" in r.json()["detail"]


def test_asr_transcribes_via_provider(client, seeded, make_room):
    from app.deps import get_asr_provider
    from app.main import app

    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.patch(
        "/system/asr-model",
        json={"conn_id": seeded["conn"]["conn_id"], "model_name": STREAM_MODEL},
    )
    # 依赖覆盖注入演示提供方：链路（身份 → 设置 → 转写 → 文本）走通，不触网
    app.dependency_overrides[get_asr_provider] = lambda: DemoAsrProvider()
    r = client.post(f"/rooms/{rid}/asr", content=b"\x01\x02\x03\x04")
    assert r.status_code == 200
    assert r.json()["text"] == DemoAsrProvider.TEXT


def test_ws_asr_unconfigured_reports_error(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "asr_begin"})
        msg = ws.receive_json()
    assert msg["type"] == "asr_error"
    assert "尚未配置语音转写模型" in msg["message"]


def test_ws_asr_streaming_flow(client, seeded, make_room):
    import base64

    from app.deps import get_asr_provider
    from app.main import app

    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.patch(
        "/system/asr-model",
        json={"conn_id": seeded["conn"]["conn_id"], "model_name": STREAM_MODEL},
    )
    app.dependency_overrides[get_asr_provider] = lambda: DemoAsrProvider()
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "asr_begin"})
        ws.send_json({"type": "asr_audio", "data": base64.b64encode(b"\x01\x02" * 100).decode()})
        ws.send_json({"type": "asr_end"})
        events = [ws.receive_json(), ws.receive_json()]
    types = [e["type"] for e in events]
    assert types == ["asr_text", "asr_done"]
    assert events[0]["text"] == DemoAsrProvider.TEXT
    assert events[1]["text"] == DemoAsrProvider.TEXT


def test_ws_asr_rejects_duplicate_begin(client, seeded, make_room):
    from app.deps import get_asr_provider
    from app.main import app

    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    client.patch(
        "/system/asr-model",
        json={"conn_id": seeded["conn"]["conn_id"], "model_name": STREAM_MODEL},
    )
    app.dependency_overrides[get_asr_provider] = lambda: DemoAsrProvider()
    with client.websocket_connect(f"/rooms/{rid}/ws") as ws:
        ws.send_json({"type": "asr_begin"})
        ws.send_json({"type": "asr_begin"})
        msg = ws.receive_json()
    assert msg["type"] == "asr_error"
    assert "语音会话已在进行中" in msg["message"]
