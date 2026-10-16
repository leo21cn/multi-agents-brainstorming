"""视图规格补齐路由的集成测试：房间生命周期、成员与主持、结论/导出下载、配置删除与编辑。"""


def _start(client, room_id):
    return client.post(f"/rooms/{room_id}/start")


def test_start_members_and_host(client, seeded, make_room):
    room = make_room(seeded["agent"]["agent_id"])
    rid = room["room_id"]
    assert room["status"] == "待开始"

    started = _start(client, rid)
    assert started.status_code == 200
    assert started.json()["status"] == "讨论中"

    members = client.get(f"/rooms/{rid}/members").json()
    assert len(members) == 1 and members[0]["is_host"] is True

    added = client.post(f"/rooms/{rid}/join", json={"display_name": "林"})
    assert added.status_code == 201
    lin_id = added.json()["member_id"]

    host_res = client.post(f"/rooms/{rid}/host", json={"member_id": lin_id})
    assert host_res.status_code == 200
    assert host_res.json()["host_member_id"] == lin_id

    after = {m["member_id"]: m["is_host"] for m in client.get(f"/rooms/{rid}/members").json()}
    assert after[lin_id] is True
    assert after[members[0]["member_id"]] is False


def test_host_assign_rejects_foreign_member(client, seeded, make_room):
    rid = make_room(seeded["agent"]["agent_id"])["room_id"]
    r = client.post(f"/rooms/{rid}/host", json={"member_id": "mem_missing"})
    assert r.status_code == 422


def _discuss(client, rid, agent_id):
    _start(client, rid)
    client.post(f"/rooms/{rid}/messages", json={"content": "请各位先给第一判断"})
    client.post(f"/rooms/{rid}/speak", json={"agent_id": agent_id, "intent": "延期 砍范围"})


def test_conclusion_get_and_download(client, seeded, make_room):
    rid = make_room(seeded["agent"]["agent_id"])["room_id"]
    _discuss(client, rid, seeded["agent"]["agent_id"])

    assert client.get(f"/rooms/{rid}/conclusion").json() is None

    gen = client.post(f"/rooms/{rid}/conclusion")
    assert gen.status_code == 201
    doc_id = gen.json()["doc_id"]

    latest = client.get(f"/rooms/{rid}/conclusion").json()
    assert latest["doc_id"] == doc_id
    assert "关键观点" in latest["sections"]

    dl = client.get(f"/rooms/{rid}/conclusion/{doc_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("text/markdown")
    assert "阶段结论" in dl.text


def test_export_get_and_download(client, seeded, make_room):
    rid = make_room(seeded["agent"]["agent_id"])["room_id"]
    _discuss(client, rid, seeded["agent"]["agent_id"])

    exp = client.post(f"/rooms/{rid}/export")
    assert exp.status_code == 201
    export_id = exp.json()["export_id"]
    assert exp.json()["entry_count"] == 2

    latest = client.get(f"/rooms/{rid}/export").json()
    assert latest["export_id"] == export_id

    dl = client.get(f"/rooms/{rid}/export/{export_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("text/plain")
    assert dl.text.count("\n") >= 1


def test_download_missing_artifact_404(client, seeded, make_room):
    rid = make_room(seeded["agent"]["agent_id"])["room_id"]
    assert client.get(f"/rooms/{rid}/conclusion/conclusion_x/download").status_code == 404
    assert client.get(f"/rooms/{rid}/export/export_x/download").status_code == 404


def test_delete_document_removes_row(client, seeded):
    docs = client.get("/documents").json()
    assert len(docs) == 1
    doc_id = docs[0]["doc_id"]

    r = client.delete(f"/documents/{doc_id}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/documents").json() == []
    assert client.delete(f"/documents/{doc_id}").status_code == 404


def test_delete_guards_and_cascade(client, seeded, make_room):
    conn_id = seeded["conn"]["conn_id"]
    agent_id = seeded["agent"]["agent_id"]

    # 被引用时拒绝
    assert client.delete(f"/model-connections/{conn_id}").status_code == 409

    make_room(agent_id)
    assert client.delete(f"/agents/{agent_id}").status_code == 409

    # 新建未被引用者，可删除
    conn2 = client.post(
        "/model-connections",
        json={
            "name": "备用",
            "provider_type": "OpenAI 兼容",
            "base_url": "http://y",
            "api_key": "sk-yyyyyyyyyyyy0000",
            "available_models": ["qwen-turbo"],
        },
    ).json()
    assert client.delete(f"/model-connections/{conn2['conn_id']}").status_code == 200

    agent2 = client.post(
        "/agents",
        json={
            "name": "临时体",
            "model_connection_id": conn_id,
            "model_name": "qwen-plus",
            "kb_ids": [seeded["kb"]["kb_id"]],
            "persona": "临时人设",
            "expertise": "临时专长",
        },
    ).json()
    assert client.delete(f"/agents/{agent2['agent_id']}").status_code == 200
    assert all(a["agent_id"] != agent2["agent_id"] for a in client.get("/agents").json())


def test_delete_knowledge_base_guards_and_cascades(client, seeded):
    # seeded 知识库被 seeded 智能体绑定 -> 拒绝
    bound_kb = seeded["kb"]["kb_id"]
    assert client.delete(f"/knowledge-bases/{bound_kb}").status_code == 409

    # 新建未绑定知识库并上传文档 -> 删除级联清除文档
    kb2 = client.post(
        "/knowledge-bases", json={"name": "临时库", "dimension": "业务领域"}
    ).json()
    client.post(
        "/documents",
        data={"kb_id": kb2["kb_id"]},
        files={"file": ("t.md", "内容 测试 语料", "text/markdown")},
    )
    assert any(d["kb_id"] == kb2["kb_id"] for d in client.get("/documents").json())

    r = client.delete(f"/knowledge-bases/{kb2['kb_id']}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert all(d["kb_id"] != kb2["kb_id"] for d in client.get("/documents").json())
    assert all(k["kb_id"] != kb2["kb_id"] for k in client.get("/knowledge-bases").json())
    assert client.delete(f"/knowledge-bases/{kb2['kb_id']}").status_code == 404


def test_patch_agent_prompt_fields(client, seeded):
    agent_id = seeded["agent"]["agent_id"]
    r = client.patch(
        f"/agents/{agent_id}",
        json={"persona": "首席架构师", "speaking_style": "先定性再给方案"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["persona"] == "首席架构师"
    assert body["speaking_style"] == "先定性再给方案"
    assert body["expertise"] == "技术债"
    assert client.patch("/agents/agent_x", json={"persona": "x"}).status_code == 404
