"""意愿判定：一次轻量模型调用，问某个智能体对本次发言是否有话要说。

判定不产生发言，只产生一条待发言举手，放行权仍在主持人手里——自主性停在「表达意愿」
这一层，编排权不下放。
"""

import json
from typing import NamedTuple

from app.models.provider import WILLINGNESS_ASK, WILLINGNESS_MARK

INTENT_MAX_CHARS = 120


class Willingness(NamedTuple):
    want: bool
    intent: str | None


def build_willingness_prompt(
    name: str, expertise: str, last_message: str, speaker: str | None = None
) -> list[dict]:
    """判定提示词：只问要不要说、想说什么，不给发言档位，也不要正文。

    发言人署名进系统段而不是提问段：离线提供方按提问段的固定前缀还原最新发言原文，
    署名混进去会改变它的判定输入，令既有的离线可复现判定漂移。
    """
    who = f"最新发言来自{speaker}。" if speaker else ""
    return [
        {
            "role": "system",
            "content": (
                f"{WILLINGNESS_MARK}你是{name}，专长：{expertise}。\n"
                "你在一个人类主持的头脑风暴房间里。判断针对最新发言你是否有话要说：只在它与你的"
                "专长相关、且你能补出一个尚未有人说过的角度时才举手；无新角度、或只是想附和时不举手。\n"
                f"{who}"
                '只输出一行 JSON，形如 {"want": true, "intent": "一句话说明你想补充什么"}；'
                "不举手时 want 为 false、intent 为 null。JSON 以外不要输出任何内容。"
            ),
        },
        {"role": "user", "content": f"{WILLINGNESS_ASK}{last_message}"},
    ]


def parse_willingness_reply(text: str) -> Willingness:
    """严格解析判定回复：取出一段 JSON 对象且 want 严格为真才算举手，否则不举手。

    容许代码围栏与前后赘语（真实模型常加），但不猜：判定是旁路增强，把模型的一句废话
    当成举手，主持人会看到一条来源不明的举手并误以为是智能体的真实意愿。
    """
    body = (text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return Willingness(False, None)
    try:
        data = json.loads(body[start : end + 1])
    except ValueError:
        return Willingness(False, None)
    if not isinstance(data, dict) or data.get("want") is not True:
        return Willingness(False, None)
    intent = data.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        return Willingness(True, None)
    return Willingness(True, intent.strip()[:INTENT_MAX_CHARS])


class WillingnessService:
    """判定服务：关思考、限时、不设长度上限。

    思考会让每条新发言的响应时间成倍增加，而判定只要一行 JSON；限时是硬界，超时与
    异常一律按不举手处理，模型故障不该阻断讨论。
    """

    def __init__(self, provider_factory, timeout: float = 15.0):
        self.provider_factory = provider_factory
        self.timeout = timeout

    def check(self, agent, last_message: str, speaker: str | None = None) -> Willingness:
        if not (last_message or "").strip():
            return Willingness(False, None)
        provider = self.provider_factory(agent)
        reply = provider.chat(
            agent.model_name,
            build_willingness_prompt(agent.name, agent.expertise or "", last_message, speaker),
            None,
            timeout=self.timeout,
            extra_body={"enable_thinking": False},
        )
        return parse_willingness_reply(reply)
