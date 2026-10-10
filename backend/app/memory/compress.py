class RollingSummary:
    """滚动摘要：把较早消息折叠为一段摘要文本（本版实现）。"""

    def compress(self, messages) -> str:
        texts = []
        for m in messages:
            content = m["content"] if isinstance(m, dict) else m.content
            if content and content.strip():
                texts.append(content.strip())
        return "；".join(texts)[:600]
