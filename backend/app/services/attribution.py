def annotate(items: list[dict], source_of: dict) -> list[dict]:
    """为每条观点附加来源（智能体与知识库）。items 每项含 agent 字段。"""
    out: list[dict] = []
    for item in items:
        src = source_of.get(item.get("agent"), {})
        out.append(
            {**item, "source": {"agent": src.get("agent", item.get("agent")), "kbs": src.get("kbs", [])}}
        )
    return out


def format_inline(annotation: dict) -> str:
    """来源标注的呈现形式：只呈现智能体名。

    知识库保留在来源标注数据里供追溯，界面与 Markdown 文档不呈现——
    标注标签要的是「这是谁说的」，知识库归属在看阵容与落地页时已经清楚。
    """
    return annotation.get("agent") or ""
