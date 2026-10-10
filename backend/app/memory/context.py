def assemble_context(
    proposition: str,
    l2_summary: str | None,
    recent_messages: list[dict],
    stance: str | None,
    kb_hits,
    budget: int,
) -> list[dict]:
    """按 L0 命题 → L2 摘要 → L1 近期原文 → L3 立场 → 知识检索 组装，超预算按序裁剪。"""
    blocks: list[tuple[str, str]] = [("system", f"命题：{proposition}")]
    if l2_summary:
        blocks.append(("system", f"讨论摘要：{l2_summary}"))
    for m in recent_messages:
        blocks.append((m["role"], m["content"]))
    if stance:
        blocks.append(("system", f"你此前的立场要点：{stance}"))
    for h in kb_hits or []:
        blocks.append(("system", f"[{h.kb_id}] {h.text}"))

    out: list[dict] = []
    total = 0
    for role, content in blocks:
        if out and total + len(content) > budget:
            break
        out.append({"role": role, "content": content})
        total += len(content)
    return out
