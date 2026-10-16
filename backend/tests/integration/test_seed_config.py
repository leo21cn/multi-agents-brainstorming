from scripts.seed_config import seed


def test_seed_builds_agents_with_kbs(tmp_path):
    counts = seed(tmp_path / "t.db", tmp_path / "data")
    assert counts["agents"] == 8
    assert counts["unbound_agents"] == 0
    assert counts["knowledge_bases"] == 3
    assert counts["connections"] == 1
