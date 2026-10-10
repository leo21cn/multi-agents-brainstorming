from app.domain.enums import KnowledgeDimension, OrchestrationMode, RoomStatus


def test_dimensions_are_three():
    assert [d.value for d in KnowledgeDimension] == [
        "业务领域",
        "项目管理与软件工程方法论",
        "技术架构",
    ]


def test_default_mode_is_human_moderated():
    assert OrchestrationMode.HUMAN_MODERATED.value == "人类主持控制"


def test_room_status_values():
    assert [s.value for s in RoomStatus] == ["待开始", "讨论中", "已结束"]
