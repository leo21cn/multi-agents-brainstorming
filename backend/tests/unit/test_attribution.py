from app.services.attribution import annotate, format_inline


def test_annotate_and_format():
    out = annotate(
        [{"agent": "架构师", "point": "砍范围"}],
        source_of={"架构师": {"agent": "架构师", "kbs": ["技术架构库"]}},
    )
    assert out[0]["point"] == "砍范围"
    assert format_inline(out[0]["source"]) == "架构师 · 技术架构库"
