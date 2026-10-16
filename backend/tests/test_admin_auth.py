from fastapi.testclient import TestClient

from app.main import app

CONN = {
    "name": "DashScope 主",
    "provider_type": "OpenAI 兼容",
    "base_url": "http://x",
    "api_key": "sk-xxxxxxxxxxxx3f9a",
    "available_models": ["qwen-max"],
}


def _bare():
    """不带令牌的客户端（env 为 autouse，覆盖与播种仍生效）。"""
    return TestClient(app)


def _with(token):
    return TestClient(app, headers={"X-Admin-Token": token})


# ---------- 默认令牌 ----------
def test_default_admin_token_is_111111(env):
    assert env.config.get_admin_token() == "111111"


# ---------- 受保护接口 ----------
def test_protected_endpoints_require_token():
    c = _bare()
    assert c.get("/model-connections").status_code == 401
    assert c.get("/documents").status_code == 401
    assert c.post("/model-connections", json=CONN).status_code == 401
    assert c.post("/knowledge-bases", json={"name": "x", "dimension": "技术架构"}).status_code == 401
    assert c.post(
        "/agents",
        json={
            "name": "a",
            "model_connection_id": "c",
            "model_name": "qwen-max",
            "kb_ids": ["k"],
            "persona": "p",
            "expertise": "e",
        },
    ).status_code == 401


def test_wrong_token_rejected():
    assert _with("nope").get("/model-connections").status_code == 401


def test_correct_token_accepted():
    c = _with("111111")
    assert c.post("/model-connections", json=CONN).status_code == 201
    assert c.get("/model-connections").status_code == 200


# ---------- 开放接口 ----------
def test_open_reads_stay_open():
    c = _bare()
    assert c.get("/system/init-status").status_code == 200
    assert c.get("/agents").status_code == 200
    assert c.get("/knowledge-bases").status_code == 200


# ---------- 登录 ----------
def test_login_ok_and_reject():
    c = _bare()
    r = c.post("/admin/login", json={"token": "111111"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert c.post("/admin/login", json={"token": "wrong"}).status_code == 401


# ---------- 修改令牌 ----------
def test_change_token_requires_auth():
    assert _bare().patch("/admin/token", json={"new_token": "222222"}).status_code == 401


def test_change_token_rejects_empty(client):
    assert client.patch("/admin/token", json={"new_token": "   "}).status_code == 422


def test_non_ascii_token_rejected_and_login_robust(client):
    # 非 ASCII 值无法经 HTTP 头编码，设置须被拒；登录侧不得 500
    assert client.patch("/admin/token", json={"new_token": "火花会"}).status_code == 422
    assert client.post("/admin/login", json={"token": "火花会"}).status_code == 401
    assert _with("111111").get("/model-connections").status_code == 200


def test_change_token_rotates_access(client):
    assert client.patch("/admin/token", json={"new_token": "222222"}).status_code == 200
    # 旧令牌失效，新令牌生效
    assert _with("111111").get("/model-connections").status_code == 401
    assert _with("222222").get("/model-connections").status_code == 200
    # 登录同样切换到新令牌
    assert _bare().post("/admin/login", json={"token": "222222"}).status_code == 200
    assert _bare().post("/admin/login", json={"token": "111111"}).status_code == 401
