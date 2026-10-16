def assemble_context(
    proposition: str,
    l2_summary: str | None,
    recent_messages: list[dict],
    stance: str | None,
    kb_hits,
    budget: int,
    focus: str | None = None,
) -> list[dict]:
    """按 L0 命题与当前焦点 → L2 摘要 → L1 近期原文 → L3 立场 → 知识检索 组装，超预算按序裁剪。"""
    blocks: list[tuple[str, str]] = [("system", f"命题：{proposition}")]
    if focus:
        # 焦点紧跟命题：裁剪按块序 break，靠后的块先被牺牲，而定向提问必须进入上下文
        blocks.append(("system", f"当前焦点：{focus}"))
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
