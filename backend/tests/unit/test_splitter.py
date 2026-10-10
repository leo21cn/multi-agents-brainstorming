from app.rag.splitter import split_text


def test_split_overlaps_and_covers():
    text = "。".join(f"句{i}" for i in range(200))
    parts = split_text(text, size=100, overlap=20)
    assert len(parts) >= 2
    assert all(len(p) <= 100 for p in parts)
    assert parts[0][-10:] in parts[1][:30]


def test_split_rejects_bad_overlap():
    import pytest

    with pytest.raises(ValueError):
        split_text("x", size=10, overlap=10)
