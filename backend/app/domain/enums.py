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


class RaiseHandStatus(str, Enum):
    QUEUED = "排队"
    RELEASED = "已放行"
    DISMISSED = "已驳回"


class SourceType(str, Enum):
    HUMAN = "人类"
    AGENT = "智能体"


class DocumentStatus(str, Enum):
    PENDING = "待处理"
    INGESTED = "已入库"
    FAILED = "失败"
