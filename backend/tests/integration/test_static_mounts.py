def test_vendor_markdown_it_is_served(client):
    r = client.get("/assets/vendor/markdown-it.min.js")
    assert r.status_code == 200
    assert len(r.content) > 100_000
    head = r.content[:200]
    assert b"markdown-it 14.1.0" in head
    assert b"@license MIT" in head


def test_vendor_provenance_matches_asset(client):
    """出处记录与实文件必须对得上，避免只改文件不改记录。"""
    import hashlib
    import pathlib

    vendor = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "assets" / "vendor"
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    digest = hashlib.sha256((vendor / "markdown-it.min.js").read_bytes()).hexdigest()
    assert digest in readme
    assert "14.1.0" in readme


def test_room_page_loads_vendor_renderer(client):
    body = client.get("/room/", follow_redirects=True).text
    assert "/assets/vendor/markdown-it.min.js" in body
    assert body.index("/assets/vendor/markdown-it.min.js") < body.index("/assets/api.js")


def test_admin_page_does_not_load_vendor_renderer(client):
    assert "/assets/vendor/" not in client.get("/admin/", follow_redirects=True).text


def test_launcher_does_not_load_vendor_renderer(client):
    assert "/assets/vendor/" not in client.get("/app", follow_redirects=True).text


def test_lobby_page_serves_create_room_and_room_list(client):
    """建房与已有房间列表已拆分到 /lobby/：大厅不加载发言渲染器，进入房间跳转 /room/?id=。"""
    body = client.get("/lobby/", follow_redirects=True).text
    assert "/assets/vendor/" not in body
    assert 'id="newProp"' in body
    assert 'id="roomList"' in body
    assert "/room/?id=" in body


def test_room_page_links_back_to_lobby_without_id(client):
    body = client.get("/room/", follow_redirects=True).text
    assert "location.replace('/lobby/')" in body
