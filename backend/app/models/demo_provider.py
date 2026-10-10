"""离线演示提供方。

无真实模型连接凭据时用于本地跑通闭环；一旦模型连接配置了 API Key，
运行期改用 OpenAI 兼容提供方（见 app/deps.py 的提供方工厂）。
"""

from app.rag.embeddings import hash_embedding


class DemoProvider:
    def chat(self, model_name: str, messages: list[dict], **kw) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        retrieved = next((m["content"] for m in messages if m["content"].startswith("[")), "")
        last = messages[-1]["content"] if messages else ""
        who = system.split("。")[0] or "智能体"
        note = f"参考检索语料：{retrieved[:40]}" if retrieved else "未命中知识库语料"
        return f"{who} 的判断：{note}；结合当前讨论，倾向先暴露分歧再收敛（依据：{last[:40]}）。"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embedding(t) for t in texts]
