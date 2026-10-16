"""发言档位与思考控制能力的判定。

档位是供应商无关的意图；思考预算只有部分模型认，故能力判定与降级都在这一层完成，
调用方只取解析结果，不接触各家参数名。降级必须置标记，由界面与阶段文案显式呈现，
不静默——静默降级会让「选了深思却按开思考不限预算跑」这类偏差无从发现。
"""

from dataclasses import dataclass

from app.domain.enums import SpeechPace

# 档位 → (思考预算 token 数, 正文字数目标)；预算为 None 表示关思考。
# 目标是提示词下达的范围，由模型自行收束；客户端只在其 1.5 倍的安全界处兜底断流。
# 快闪档关思考取自真实端点实测的首正文延迟差：关思考 0.6s，开思考约 7s。
PACE_PARAMS: dict[SpeechPace, tuple[int | None, int]] = {
    SpeechPace.FLASH: (None, 240),
    SpeechPace.BALANCED: (256, 600),
    SpeechPace.DEEP: (1024, 1800),
}

# 安全界相对提示词目标的上浮倍数：只防模型无视要求时无限输出，不作日常卡点。
CEILING_RATIO = 1.5

DEFAULT_PACE = SpeechPace.BALANCED

# 支持思考预算的模型名前缀。模型名是统一 OpenAI 兼容端点下唯一可得的供应商标识：
# ModelConnection.provider_type 是自由字符串（种子值为「OpenAI 兼容」），不能作能力表的键。
# 前缀判不出代际（qwen-plus 在 DashScope 上跨代复用），故端点拒绝时另有可见降级路径兜底。
THINKING_BUDGET_PREFIXES = ("qwen",)


@dataclass(frozen=True)
class ResolvedPace:
    pace: SpeechPace
    thinking_enabled: bool
    thinking_budget: int | None
    max_chars: int
    budget_supported: bool
    degraded: bool

    @property
    def ceiling_chars(self) -> int:
        """安全界：提示词目标的上浮兜底，仅防模型无视要求时无限输出。"""
        return int(self.max_chars * CEILING_RATIO)


def supports_thinking_budget(model_name: str) -> bool:
    name = (model_name or "").strip().lower()
    return name.startswith(THINKING_BUDGET_PREFIXES)


def resolve(pace: SpeechPace | None, model_name: str) -> ResolvedPace:
    p = pace or DEFAULT_PACE
    budget, max_chars = PACE_PARAMS[p]
    supported = supports_thinking_budget(model_name)
    if budget is None:
        return ResolvedPace(p, False, None, max_chars, supported, False)
    if supported:
        return ResolvedPace(p, True, budget, max_chars, supported, False)
    return ResolvedPace(p, True, None, max_chars, supported, True)
