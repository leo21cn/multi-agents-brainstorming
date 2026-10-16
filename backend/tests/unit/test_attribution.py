from app.services.attribution import annotate, format_inline


def test_annotate_and_format():
    out = annotate(
        [{"agent": "架构师", "point": "砍范围"}],
        source_of={"架构师": {"agent": "架构师", "kbs": ["技术架构库"]}},
    )
    assert out[0]["point"] == "砍范围"
    # 知识库留在数据里供追溯，呈现只取智能体名
    assert out[0]["source"] == {"agent": "架构师", "kbs": ["技术架构库"]}
    assert format_inline(out[0]["source"]) == "架构师"
