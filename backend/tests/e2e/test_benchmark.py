from pathlib import Path

from scripts.run_benchmark import run

PROPS = str(Path(__file__).resolve().parents[3] / "scripts" / "benchmark_propositions.yaml")


def test_benchmark_reports_metrics(tmp_path):
    report = run(PROPS, work_dir=str(tmp_path))
    assert report["propositions"] >= 1
    assert report["source_consistency_rate"] >= 0.9
    assert report["annotation_completeness"] == 1.0
    assert report["unauthorized_speech"] == 0
    assert report["export_count_match"] is True
