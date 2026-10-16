from fastapi.testclient import TestClient

from app.main import app


def test_ui_options_open_read_defaults_to_shown(client):
    r = client.get("/system/ui-options")
    assert r.status_code == 200
    body = r.json()
    assert body["show_raise_sim"] is True
    assert "auto_raise" in body


def test_ui_options_readable_without_token(env):
    """选项不是敏感信息，房间页以开放接口读取。"""
    bare = TestClient(app)
    r = bare.get("/system/ui-options")
    assert r.status_code == 200 and r.json()["show_raise_sim"] is True
    # conftest 为隔离关掉自主举手，故此处读到关；产品缺省开见下方单测
    assert r.json()["auto_raise"] is False


def test_ui_options_open_read_never_carries_the_token(client):
    r = client.get("/system/ui-options")
    assert "token" not in r.json()
    assert "111111" not in r.text


def test_ui_options_write_requires_token(env):
    bare = TestClient(app)
    r = bare.patch("/system/ui-options", json={"show_raise_sim": False})
    assert r.status_code == 401
    assert env.config.get_show_raise_sim() is True


def test_ui_options_round_trip(client, env):
    r = client.patch("/system/ui-options", json={"show_raise_sim": False})
    assert r.status_code == 200 and r.json()["show_raise_sim"] is False
    assert env.config.get_show_raise_sim() is False
    assert client.get("/system/ui-options").json()["show_raise_sim"] is False

    client.patch("/system/ui-options", json={"show_raise_sim": True})
    assert env.config.get_show_raise_sim() is True


def test_auto_raise_defaults_to_on_without_seeding(tmp_path):
    """产品缺省开：既有库没有这条设置也按开处理，不靠迁移播种。"""
    from app.persistence.db import get_engine, init_db
    from app.services.config_service import ConfigService
    from app.settings import Settings

    engine = get_engine(Settings("", str(tmp_path / "c.db"), str(tmp_path), 6000, 8000))
    init_db(engine)
    svc = ConfigService(engine)

    assert svc.get_auto_raise() is True
    svc.set_auto_raise(False)
    assert svc.get_auto_raise() is False
    svc.set_auto_raise(True)
    assert svc.get_auto_raise() is True


def test_auto_raise_round_trip(client, env):
    r = client.patch("/system/ui-options", json={"show_raise_sim": True, "auto_raise": True})
    assert r.status_code == 200 and r.json()["auto_raise"] is True
    assert env.config.get_auto_raise() is True

    client.patch("/system/ui-options", json={"show_raise_sim": True, "auto_raise": False})
    assert env.config.get_auto_raise() is False
    assert client.get("/system/ui-options").json()["auto_raise"] is False
