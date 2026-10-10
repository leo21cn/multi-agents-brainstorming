from pathlib import Path

from scripts.seed_corpus import DIMENSIONS, find_testdata, ingest


def test_corpus_covers_three_dimensions():
    assert DIMENSIONS == {"业务领域", "项目管理与软件工程方法论", "技术架构"}
    td = find_testdata()
    for sub in ("business", "methodology", "architecture"):
        assert (td / sub).is_dir()


def test_ingest_reads_testdata_and_creates_chunks(tmp_path):
    counts = ingest(find_testdata(), tmp_path / "t.db", tmp_path / "data")
    assert set(counts) == DIMENSIONS
    assert all(v > 0 for v in counts.values())
