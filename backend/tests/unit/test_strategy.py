import pytest

from app.orchestration.strategy import ManualStrategy, RoundRobinStrategy


def test_manual_returns_none_without_directive():
    assert ManualStrategy().select_next({"directive": None}) is None


def test_manual_returns_pointed_agent():
    assert ManualStrategy().select_next({"directive": {"agent_id": "a1"}}) == "a1"


def test_other_modes_not_implemented():
    with pytest.raises(NotImplementedError):
        RoundRobinStrategy().select_next({})
