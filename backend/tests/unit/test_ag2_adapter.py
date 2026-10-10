from app.orchestration.ag2_adapter import Ag2Adapter


class Spy:
    def __init__(self):
        self.seen = None

    def select_next(self, state):
        self.seen = state
        return "a1"


def test_adapter_delegates_to_strategy():
    spy = Spy()
    r = Ag2Adapter().run_turn({"directive": {"agent_id": "a1"}}, spy)
    assert r.next_speaker == "a1"
    assert spy.seen == {"directive": {"agent_id": "a1"}}
