"""离线演示提供方。

无真实模型连接凭据时用于本地跑通闭环；一旦模型连接配置了 API Key，
运行期改用 OpenAI 兼容提供方（见 app/deps.py 的提供方工厂）。

不产思考片段：离线提供方没有推理过程，伪造一段会误导人工核对。
"""

import json
import zlib
from typing import TYPE_CHECKING, Iterator

from app.models.cutoff import cut_at_boundary
from app.models.provider import (
    CONTENT,
    CONCLUSION_ASK,
    CONCLUSION_MARK,
    END,
    WILLINGNESS_ASK,
    WILLINGNESS_MARK,
    StreamPiece,
)
from app.rag.embeddings import hash_embedding

if TYPE_CHECKING:
    from app.models.thinking import ResolvedPace

# 与 conclusion_service.FIXED_SECTION_KEYS 同值；此处另立一份，因为模型层不得反向依赖服务层
_FIXED_TITLES = ("命题", "关键观点", "分歧点", "初步结论／建议")


class DemoProvider:
    @staticmethod
    def _render(messages: list[dict], pace: "ResolvedPace | None") -> tuple[str, bool]:
        """组装演示正文并按安全界收尾；返回（保留部分，是否有丢弃）。

        两条路径共用这一次切分，故一次性与流式的正文必然一致。
        """
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        retrieved = next((m["content"] for m in messages if m["content"].startswith("[")), "")
        last = messages[-1]["content"] if messages else ""
        who = system.split("。")[0] or "智能体"
        note = f"参考检索语料：{retrieved[:40]}" if retrieved else "未命中知识库语料"
        text = f"{who} 的判断：{note}；结合当前讨论，倾向先暴露分歧再收敛（依据：{last[:40]}）。"
        if pace is None:
            return text, False
        return cut_at_boundary(text, pace.ceiling_chars)

    @staticmethod
    def _willingness(system: str, ask: str) -> str:
        """离线判定：同一智能体面对同一条发言结果恒定，且不同智能体不全一致。

        离线判定不能是随机数——不可复现的判定让自动化测试无法断言，也让本地演示每次
        跑出不同场面。取名字与最新发言的哈希，令「谁愿意说」由内容决定。
        """
        who = system[len(WILLINGNESS_MARK) :].removeprefix("你是").split("，")[0]
        last = ask.removeprefix(WILLINGNESS_ASK)
        want = zlib.crc32(f"{who}|{last[:80]}".encode("utf-8")) % 3 != 0
        intent = f"就「{last[:12]}」补充一点" if want else None
        return json.dumps({"want": want, "intent": intent}, ensure_ascii=False)

    @staticmethod
    def _block(payload: str, title: str, stop: str | None) -> list[str]:
        head = payload.find(title)
        if head < 0:
            return []
        seg = payload[head + len(title) :]
        tail = seg.find(stop) if stop else -1
        seg = seg[:tail] if tail >= 0 else seg
        return [ln.removeprefix("- ").strip() for ln in seg.split("\n") if ln.startswith("- ")]

    @classmethod
    def _conclusion(cls, ask: str) -> str:
        """离线综合：由提示词内的命题、署名与已抽取要点派生恒定的若干章。

        与离线判定同理，综合不能是随机文本——不可复现的结论让自动化测试无法断言，
        也让本地演示每次跑出不同文档。额外各章的标题取自要点原文，故「章数可变、
        标题自拟」这两条路径在无密钥时同样被走到。
        """
        payload = ask.removeprefix(CONCLUSION_ASK)
        prop = payload[len("命题：") :].split("\n", 1)[0] if payload.startswith("命题：") else ""
        keys = cls._block(payload, "已抽取关键观点：", "已抽取分歧点：")
        disputes = cls._block(payload, "已抽取分歧点：", None)
        speakers: list[str] = []
        head = payload.find("讨论记录：")
        seg = payload[head + len("讨论记录：") :] if head >= 0 else ""
        tail = seg.find("已抽取关键观点：")
        seg = seg[:tail] if tail >= 0 else seg
        for ln in seg.split("\n"):
            if ln.startswith("[") and "]" in ln:
                who = ln[1 : ln.index("]")]
                if who not in speakers:
                    speakers.append(who)
        kp0 = keys[0] if keys else ""
        kp1 = keys[1] if len(keys) > 1 else kp0
        dg0 = disputes[0] if disputes else ""
        p0, p1, d0 = cls._point_of(kp0), cls._point_of(kp1), cls._point_of(dg0)
        who0 = speakers[0] if speakers else "主持人"
        who1 = speakers[1] if len(speakers) > 1 else who0
        out = {
            "初步结论／建议": (
                f"围绕命题「{prop}」，讨论的收敛方向是：{kp0 or '观点仍在发散，尚无可收敛的主轴'}；"
                "建议以此为主轴收束，其余观点作为补充保留。"
            )
        }
        first = cls._extra_title(p0, "讨论主轴")
        second = cls._extra_title(d0, "待收敛的差异")
        if second == first:
            # 关键观点取发言前 60 字、分歧点取立场记忆即同一条发言前 200 字，
            # 单条发言时两者前 12 字必然相同：换一条要点，仍相同则用兜底名
            second = cls._extra_title(p1, "待收敛的差异")
        if second == first:
            second = "待收敛的差异"
        out[first] = (
            f"{who0}与{who1}的落点不同：{p0 or prop}。"
            "就这一条安排验证，结论回到本章，不在他处重述。"
        )
        out[second] = f"{d0 or '各立场的差异'}尚未收敛，需在下一轮讨论中逐条确认取舍。"
        return json.dumps(out, ensure_ascii=False)

    @staticmethod
    def _point_of(line: str) -> str:
        """要点行形如「{署名}：{要点}」，标题取要点部分，不带署名。"""
        return line.split("：", 1)[1].strip() if "：" in line else line.strip()

    @classmethod
    def _extra_title(cls, point: str, fallback: str) -> str:
        """额外章标题取要点前 12 字；空则用兜底名，撞固定章名则加前缀避免键冲突。"""
        title = (point or "").strip()[:12] or fallback
        return f"观点：{title}" if title in _FIXED_TITLES else title

    def chat(
        self, model_name: str, messages: list[dict], pace: "ResolvedPace | None" = None, **kw
    ) -> str:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if system.startswith(WILLINGNESS_MARK):
            return self._willingness(system, messages[-1]["content"] if messages else "")
        if system.startswith(CONCLUSION_MARK):
            return self._conclusion(messages[-1]["content"] if messages else "")
        return self._render(messages, pace)[0]

    def stream_chat(
        self, model_name: str, messages: list[dict], pace: "ResolvedPace | None" = None, **kw
    ) -> Iterator[StreamPiece]:
        text, dropped = self._render(messages, pace)
        step = 12
        for i in range(0, len(text), step):
            yield StreamPiece(CONTENT, text[i : i + step])
        yield StreamPiece(END, "", dropped)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embedding(t) for t in texts]
