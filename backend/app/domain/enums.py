from enum import Enum


class KnowledgeDimension(str, Enum):
    BUSINESS = "业务领域"
    METHODOLOGY = "项目管理与软件工程方法论"
    ARCHITECTURE = "技术架构"


class OrchestrationMode(str, Enum):
    HUMAN_MODERATED = "人类主持控制"
    ROUND_ROBIN = "固定轮询"
    AI_MODERATOR = "AI 主持人调度"
    FREE_FOR_ALL = "自由抢话"


class RoomStatus(str, Enum):
    PENDING = "待开始"
    IN_PROGRESS = "讨论中"
    ENDED = "已结束"


class MemberStatus(str, Enum):
    """人类成员的在线状态：退出聊天室为服务端动作，标记已退出并清令牌；认领即恢复在线。"""

    ONLINE = "在线"
    LEFT = "已退出"


class RaiseHandStatus(str, Enum):
    QUEUED = "排队"
    RELEASED = "已放行"
    DISMISSED = "已驳回"
    # 重判后智能体自己不再想说：与主持人驳回区分开，二者在统计上是两回事
    WITHDRAWN = "已撤回"


class SourceType(str, Enum):
    HUMAN = "人类"
    AGENT = "智能体"


class DocumentStatus(str, Enum):
    PENDING = "待处理"
    INGESTED = "已入库"
    FAILED = "失败"


class SpeechPace(str, Enum):
    FLASH = "快闪"
    BALANCED = "均衡"
    DEEP = "深思"
