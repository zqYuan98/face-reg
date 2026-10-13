import json
import math

from faceeval import latency, recognition, report
from faceeval.data import load_run

from synthetic import write_run


def test_threshold_at_far_never_exceeds_the_target():
    impostor = [0.1 * i for i in range(10)]  # 0.0 .. 0.9
    t, achieved, sufficient = recognition.threshold_at_far(impostor, 0.2)
    assert achieved <= 0.2
    assert sum(s >= t for s in impostor) == 2
    assert sufficient is False  # 10 * 0.2 = 2 expected false accepts < 3
    t, achieved, _ = recognition.threshold_at_far(impostor, 0.0)
    assert achieved == 0 and t > 0.9
    assert math.isnan(recognition.threshold_at_far([], 0.1)[0])


def test_tar_and_identification_on_synthetic_run(tmp_path):
    run = load_run(write_run(tmp_path / "run", subjects=30))
    data = recognition.split(run, "ours")
    assert len(data.genuine) == 30 * 3  # three feature frames per live clip
    assert len(data.impostor) == 30 * 3 * 29
    point = recognition.at_far(data, 1e-2)
    assert point.sufficient
    assert point.tar.value > 0.95
    rank1, fpir = recognition.identification(data, point.threshold)
    assert rank1.value > 0.95 and fpir.n == 0


def test_latency_excludes_warmup_and_compares_percentiles(tmp_path):
    run = load_run(write_run(tmp_path / "run", ours_ms=20.0, ref_ms=30.0))
    ours, ref = latency.stage_stats(run, "ours"), latency.stage_stats(run, "ref")
    assert ours["full_pipeline_ms"].count > 0
    assert math.isclose(ours["full_pipeline_ms"].p50, 20.0, rel_tol=1e-6)
    verdicts = latency.compare(ours, ref)
    assert verdicts["full_pipeline_ms"] == "PASS"
    assert latency.compare(ref, ours)["full_pipeline_ms"] == "WORSE"


def test_report_end_to_end(tmp_path):
    run_dir = write_run(tmp_path / "run", subjects=40, attacks_per_type=10, ref_attack_leak=0.4)
    out = tmp_path / "report.md"
    assert report.main([str(run_dir), "--ours", "ours", "--ref", "ref", "--far", "1e-2", "--min-attacks", "5",
                        "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "## 结论" in text and "## 活体" in text and "## 识别率" in text and "## 速度" in text
    summary = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert summary["verdicts"]["liveness"] == "PASS"
    assert summary["verdicts"]["speed"] == "PASS"
    assert summary["verdicts"]["recognition"] in ("PASS", "WORSE")
