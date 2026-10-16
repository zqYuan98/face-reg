"""Session reports must not turn missing decisions or wrong identities into passes."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("session_compare", Path(__file__).parents[1] / "session_compare.py")
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def row(backend="session_cascade", clip="xxr_live", subject="xxr", label="live", note="",
        status="-1", faces="1", cost="10", index=0, timestamp=None):
    return {"backend": backend, "clip_id": clip, "subject_id": subject, "label": label,
            "attack_type": "screen" if label == "attack" else "", "note": note,
            "live_status": status, "face_count": faces, "detect_ms": cost,
            "frame_index": str(index), "timestamp_ns": str(index * 100_000_000 if timestamp is None else timestamp)}


def test_wrong_identity_is_not_a_success_even_if_correct_later():
    result = reporter.summarize_session([
        row(note="recognized:yzq:0.99:300", status="1"),
        row(note="recognized:xxr:0.50:500", status="1", index=1),
    ], "xxr")
    assert result["identity_result"] == "wrong_identity"
    assert result["correct_elapsed_ms"] is None
    assert result["wrong_identity_events"] == 1


def test_correct_identity_uses_recorded_worker_time():
    result = reporter.summarize_session([row(note="recognized:xxr:0.57:422", status="1")], "xxr")
    assert result["identity_result"] == "correct"
    assert result["correct_elapsed_ms"] == 422


@pytest.mark.parametrize("note,status,faces,expected", [
    ("recognized:xxr:0.57:400", "1", "1", "authentication_accepted"),
    ("state:PENDING:NONE", "1", "1", "liveness_passed_only"),
    ("state:PENDING:NOT_ALIVE", "0", "1", "not_alive_observed"),
    ("state:PENDING:NONE", "-1", "1", "undecided"),
    ("", "-1", "0", "no_face_observed"),
    ("error: extraction failed", "-101", "0", "error"),
])
def test_attack_outcomes_are_separate(note, status, faces, expected):
    result = reporter.summarize_session([row(label="attack", note=note, status=status, faces=faces)], "xxr")
    assert result["attack_observation"] == expected


def test_later_alive_cannot_be_hidden_by_an_earlier_negative():
    result = reporter.summarize_session([row(status="0"), row(status="1", index=1)], "xxr")
    assert result["attack_observation"] == "liveness_passed_only"


def test_missing_data_never_counts_as_attack_rejection():
    result = reporter.summarize_session([], "xxr")
    assert result["attack_observation"] == "missing"
    assert result["identity_result"] == "missing"


def test_nan_error_and_dropped_rows_do_not_contaminate_costs():
    rows = [row(cost="3"), row(cost="NaN", index=1), row(cost="Infinity", index=2),
            row(cost="-2", index=3), row(cost="5", note="error: failed", index=4),
            row(cost="0", note="dropped", index=5)]
    result = reporter.summarize_session(rows, "xxr")
    assert result["costs_ms"] == [3.0]
    assert result["missing_or_invalid_timings"] == 4
    assert result["dropped_frames"] == 1
    json.dumps(result, allow_nan=False)


def test_bad_recognition_event_is_error_not_success():
    result = reporter.summarize_session([row(note="recognized:xxr:nan:400")], "xxr")
    assert result["identity_result"] == "error"
    assert result["malformed_events"] == 1


def test_subject_filter_does_not_include_previous_subject():
    rows = [row(), row(clip="yzq_live", subject="yzq"), row(backend="arcsoft")]
    report = reporter.build_report(rows, subject="xxr")
    assert set(report["clips"]) == {"xxr_live"}
    assert set(report["results"]) == {"session_cascade"}


def test_does_not_simulate_arcsoft_from_per_frame_rows():
    with pytest.raises(ValueError, match="实测"):
        reporter.build_report([row(backend="arcsoft")])
    with pytest.raises(ValueError, match="不再生成模拟"):
        reporter.build_report([row()], arcsoft_rows=[row(backend="arcsoft")])


def test_imports_real_arcsoft_session_and_checks_clip_identity():
    reference = [row(backend="session_arcsoft", note="recognized:xxr:0.92:480", status="1")]
    report = reporter.build_report([row()], arcsoft_rows=reference)
    assert report["results"]["session_arcsoft"]["xxr_live"]["identity_result"] == "correct"
    reference[0]["timestamp_ns"] = "1000"
    with pytest.raises(ValueError, match="不匹配"):
        reporter.build_report([row()], arcsoft_rows=reference)


def test_missing_backend_clip_is_explicit():
    report = reporter.build_report([row(), row(backend="session_arcsoft", clip="xxr_screen", label="attack")])
    assert report["results"]["session_cascade"]["xxr_screen"]["attack_observation"] == "missing"
    assert report["warnings"]


def test_all_invalid_timings_render_without_crashing():
    report = reporter.build_report([row(cost="NaN", note="error: boom", status="-101")])
    text = reporter.render_report(report)
    assert "错误" in text
    assert "可靠拒绝" in text
    assert "nan" not in text.lower()
    json.dumps(report, allow_nan=False)


def test_id_card_run_warns_about_comparison_configuration(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps({
        "cancelled": False, "failures": {}, "config": {"arcSoftIdCardModel": True}
    }), encoding="utf-8")
    (tmp_path / "frames.csv").write_text("backend,clip_id,subject_id,label\n", encoding="utf-8")
    _, warnings = reporter.read_run(tmp_path)
    assert any("ID_CARD" in warning and "LIFE_PHOTO" in warning for warning in warnings)


def versioned_run():
    digest = {"sha256": "a" * 64, "bytes": 10}
    models = {name: dict(digest) for name in (
        "insightface/det_500m.onnx", "insightface/w600k_mbf.onnx", "insightface/w600k_r50_int8.onnx",
        "live/config.json", "live/model_1.bin", "live/model_1.param", "live/model_2.bin", "live/model_2.param")}
    manifest = {"schemaVersion": 1, "apks": {"apk0": digest}, "inputs": {"xxr.fclip": digest},
                "enrollment": {"xxr.jpg": digest}, "expectedEnrolledIds": ["xxr"],
                "modelAssets": models, "nativeLibraries": {"engine.so": digest}}
    for group in ("inputs", "enrollment", "modelAssets"):
        manifest[group + "Sha256"] = reporter.fingerprint(manifest[group])
    config = {"backends": ["session_cascade"], "arcSoftIdCardModel": False,
              "evaluationVariant": "baseline", "finishTimeoutMs": 2000}
    effective = {"session": {"shareFeatureCapture": False}}
    completion = {"backend": "session_cascade", "clipId": "xxr_live", "epoch": 1,
                  "eof": True, "cancelled": False, "stopReason": "eof", "error": "", "drainOk": True,
                  "drainError": "", "frameErrors": 0, "timestampErrors": 0, "receivedFrames": 2,
                  "eventOverflow": 0, "eventCount": 1, "eventErrors": 0, "effectiveConfig": effective,
                  "effectiveConfigSha256": reporter.fingerprint(effective)}
    metadata = {"schemaVersion": 2, "eventSchemaVersion": 1, "finished": True,
                "finishedAtEpochMs": 10000, "complete": True, "cancelled": False, "failures": {},
                "lifePhotoChecked": True, "config": config, "configSha256": reporter.fingerprint(config),
                "manifest": manifest, "plannedBackends": ["session_cascade"],
                "plannedBackendClips": [{"backend": "session_cascade", "clipId": "xxr_live", "subjectId": "xxr",
                                         "label": "live", "attackType": "", "frameCount": 2, "durationMs": 100}],
                "actualBackendClips": [completion], "actualEnrolledIds": {"session_cascade": ["xxr"]}}
    events = [{"schema_version": "1", "backend": "session_cascade", "clip_id": "xxr_live", "epoch": "1",
               "sequence": "1", "track_id": "1", "frame_id": "1", "kind": "recognized",
               "expected_identity": "xxr", "actual_identity": "xxr", "elapsed_ms": "140", "score": "0.9",
               "live_status": "1", "error": "", "window": "tail"}]
    return reporter.RunRows([row(note="recognized:wrong:1:99"), row(index=1)], metadata, events,
                            {"schemaVersion": 1, "clips": [completion]},
                            [{"backend": "session_cascade", "enrolled_id": "xxr", "ok": "1"}])


def refresh(data):
    data.audit = reporter.audit_run(data)
    return data


def test_structured_tail_is_authoritative_and_not_double_counted_from_notes():
    data = versioned_run()
    assert data.audit["eligible"], data.audit["issues"]
    report = reporter.build_report(data)
    result = report["results"]["session_cascade"]["xxr_live"]
    assert result["identity_result"] == "correct"
    assert result["correct_elapsed_ms"] == 140
    assert len(result["events"]) == 1
    assert result["correct_recording_elapsed_ms"] is None
    assert result["correct_tail_elapsed_ms"] == 140
    assert "片尾" in reporter.render_report(report)


@pytest.mark.parametrize("change,fragment", [
    (lambda d: d.metadata.update(finished=False), "finished"),
    (lambda d: d.metadata.update(cancelled=True), "取消"),
    (lambda d: d.metadata["actualBackendClips"][0].update(drainOk=False), "drain"),
    (lambda d: d.metadata["actualBackendClips"][0].update(eventOverflow=1), "溢出"),
    (lambda d: d.metadata["actualEnrolledIds"].update(session_cascade=[]), "入库"),
    (lambda d: d[1].update(frame_index="0"), "重复"),
    (lambda d: d[1].update(timestamp_ns="0"), "时间戳"),
    (lambda d: d[1].update(note="error: native", live_status="-101"), "逐帧"),
    (lambda d: d.events[0].update(epoch="2"), "结构事件"),
    (lambda d: d.events[0].update(sequence="2"), "结构事件"),
    (lambda d: d.events[0].update(window="recording"), "结构事件"),
    (lambda d: d.metadata["manifest"]["modelAssets"].pop("insightface/w600k_mbf.onnx"), "模型资产"),
    (lambda d: d.metadata["config"].update(evaluationVariant="shared_capture"), "指纹"),
    (lambda d: d.metadata["actualBackendClips"][0]["effectiveConfig"].update(extra=1), "指纹"),
])
def test_v2_invalid_evidence_stays_observation_only(change, fragment):
    data = versioned_run()
    change(data)
    refresh(data)
    assert not data.audit["eligible"]
    assert any(fragment in issue for issue in data.audit["issues"]), data.audit["issues"]
    json.dumps(reporter.build_report(data), allow_nan=False)


def test_completely_missing_planned_backend_is_not_hidden():
    data = versioned_run()
    plan = dict(data.metadata["plannedBackendClips"][0], backend="session_arcsoft")
    data.metadata["plannedBackendClips"].append(plan)
    data.metadata["plannedBackends"].append("session_arcsoft")
    data.metadata["config"]["backends"].append("session_arcsoft")
    refresh(data)
    report = reporter.build_report(data)
    assert report["results"]["session_arcsoft"]["xxr_live"]["identity_result"] == "missing"
    assert not report["audit"]["eligible"]


def test_liveness_tail_without_identity_is_not_reliable_rejection():
    data = versioned_run()
    data.events[0].update(kind="liveness", actual_identity="", live_status="1")
    result = reporter.build_report(refresh(data))["results"]["session_cascade"]["xxr_live"]
    assert result["identity_result"] == "no_decision"
    assert result["attack_observation"] == "liveness_passed_only"


@pytest.mark.parametrize("group", ["inputs", "enrollment", "modelAssets", "apks"])
def test_cross_run_fingerprint_mismatch_is_explicit(group):
    a, b = versioned_run(), versioned_run()
    b.metadata["manifest"][group] = {"changed": {"sha256": "b" * 64, "bytes": 11}}
    assert any(group in issue for issue in reporter.compare_run_evidence(a, b))


def test_legacy_data_remains_readable_but_not_strict(tmp_path):
    (tmp_path / "frames.csv").write_text("backend,clip_id,subject_id,label,frame_index,timestamp_ns\n"
                                         "session_cascade,old,xxr,live,0,0\n", encoding="utf-8")
    rows, warnings = reporter.read_run(tmp_path)
    assert warnings
    assert not reporter.build_report(rows)["audit"]["eligible"]
