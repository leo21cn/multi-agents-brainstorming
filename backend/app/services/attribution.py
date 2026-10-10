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
    agent = annotation.get("agent") or ""
    kbs = annotation.get("kbs") or []
    return f"{agent} · {'、'.join(kbs)}" if kbs else agent
