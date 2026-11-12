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


def timed_row(index=0, state="ANALYZED", mode="FULL", cost="10"):
    result = dict.fromkeys(reporter.FRAME_COLUMNS_V3, "")
    result.update(row(index=index, faces="0", cost=cost))
    result.update(analysis_status=state, detector_mode="NONE", detector_status="UNAVAILABLE",
                  feature_attempted="0", feature_ok="0", warmup="0")
    if state == "ANALYZED":
        result.update(detector_mode=mode, detector_status="OK", full_calls="1" if mode == "FULL" else "0",
                      roi_calls="1" if mode == "ROI" else "0", copy_ms="1", sample_ms="1",
                      scrfd_preprocess_ms="1", scrfd_inference_ms="2", scrfd_postprocess_ms="1",
                      full_ms="7" if mode == "FULL" else "", roi_ms="7" if mode == "ROI" else "",
                      preprocess_ms="8", analysis_cpu_ms="4", feature_capture_count="0",
                      liveness_capture_count="0", copy_bytes="24", copy_reallocations="0")
    elif state == "DROPPED":
        result.update(note="dropped", detect_ms="")
    return result


def timed_run(frames=None):
    data = versioned_run()
    data[:] = frames if frames is not None else [timed_row(), timed_row(1, mode="ROI")]
    profile = reporter.expected_analysis_profile("baseline")
    data.metadata.update(schemaVersion=3, timingSchemaVersion=1, framesColumns=list(reporter.FRAME_COLUMNS_V3),
                         analysisProfile=profile, profileSha256=reporter.fingerprint(profile))
    data.frame_columns = list(reporter.FRAME_COLUMNS_V3)
    complete = data.metadata["actualBackendClips"][0]
    effective = complete["effectiveConfig"]
    effective["analysisProfile"] = profile
    effective["detector"] = {"longSide": 320, "threshold": .5, "fullScanEvery": 10,
                             "regionLongSide": 160, "regionScale": 2.0, "regionMinIou": .2,
                             "detectorThreads": 2, "livenessThreads": 2, "timingLevel": "detector_stages_v1"}
    complete["effectiveConfigSha256"] = reporter.fingerprint(effective)
    complete.update(receivedFrames=len(data), droppedFrames=sum(r["analysis_status"] == "DROPPED" for r in data))
    complete["metrics"] = {
        "analysisCounts": {state: sum(r["analysis_status"] == state for r in data) for state in reporter.ANALYSIS_STATES},
        "featureCaptureCount": sum(reporter.csv_count(r.get("feature_capture_count")) or 0 for r in data),
        "livenessCaptureCount": sum(reporter.csv_count(r.get("liveness_capture_count")) or 0 for r in data),
    }
    duration = (int(data[-1]["timestamp_ns"]) - int(data[0]["timestamp_ns"])) / 1e6 if data else 0
    data.metadata["plannedBackendClips"][0].update(frameCount=len(data), durationMs=duration)
    data.events[0].update(elapsed_ms=str(duration + 40))
    return refresh(data)


@pytest.mark.parametrize("variant", ["baseline", "shared_capture"])
def test_v3_profile_cross_language_canonical_payload(variant):
    # Matched by AnalysisProfileFingerprintTest in the Android bench module.
    canonical = ('{"detectorLongSide":320,"detectorThreads":2,"detectorThreshold":0.5,'
                 '"earlyFrameAdmission":false,"featureThreads":4,"fullScanEvery":10,"landmarkThreads":1,'
                 '"livenessThreads":2,"regionLongSide":160,"regionMinIou":0.2,"regionScale":2.0,'
                 '"shareFeatureCapture":false,"timingLevel":"detector_stages_v1","variant":"baseline","version":1}')
    if variant == "shared_capture":
        canonical = canonical.replace('"shareFeatureCapture":false', '"shareFeatureCapture":true')
        canonical = canonical.replace('"variant":"baseline"', '"variant":"shared_capture"')
    assert json.dumps(reporter.expected_analysis_profile(variant), sort_keys=True, separators=(",", ":")) == canonical


def test_v3_full_roi_and_stage_counts_are_audited():
    data = timed_run()
    assert data.audit["eligible"], data.audit["issues"]
    result = reporter.build_report(data)
    analysis = result["results"]["session_cascade"]["xxr_live"]["analysis"]
    assert analysis["successful_admitted_onframe"]["count"] == 2
    assert analysis["modes"]["FULL"]["fraction_of_analyzed"] == .5
    assert analysis["modes"]["ROI"]["frames"] == 1
    assert analysis["stages"]["full_ms"]["count"] == 1
    assert analysis["stages"]["feature_ms"]["count"] == 0
    assert analysis["all_call_wall_ms"] == 20
    assert analysis["call_wall_ms_per_recorded_second"] == 200
    text = reporter.render_report(result)
    assert "不混入 REJECTED" in text and "不是实测回放墙钟占用率" in text
    assert "连续帧不是独立样本" in text and "不包含 ORT 线程池" in text
    json.dumps(result, allow_nan=False)


def test_v3_rejected_and_dropped_do_not_lower_admitted_percentiles():
    data = timed_run([timed_row(cost="80"), timed_row(1, state="REJECTED", cost="0.001"),
                      timed_row(2, state="DROPPED")])
    assert data.audit["eligible"], data.audit["issues"]
    result = reporter.summarize_session(data, "xxr", data.events)
    assert result["costs_ms"] == [80., .001]  # Legacy field retains its meaning.
    analysis = result["analysis"]
    assert analysis["successful_admitted_costs_ms"] == [80.]
    assert analysis["successful_admitted_onframe"]["p50_ms"] == 80
    assert analysis["all_call_wall_ms"] == 80.001
    assert analysis["all_call_count"] == 2
    assert analysis["status_counts"]["DROPPED"] == 1


@pytest.mark.parametrize("column,value,fragment", [
    ("analysis_status", "", "analysis_status"),
    ("analysis_status", "UNMEASURED", "自研 session"),
    ("detector_status", "UNAVAILABLE", "成功检测"),
    ("detector_mode", "TRACK", "FULL/ROI"),
    ("full_calls", "0", "FULL/ROI"),
    ("roi_calls", "1", "FULL/ROI"),
    ("roi_ms", "0", "FULL/ROI"),
    ("full_ms", "", "FULL/ROI"),
    ("copy_bytes", "0", "复制字节"),
    ("copy_reallocations", "0.0", "整数计数"),
    ("feature_capture_count", "1e0", "整数计数"),
    ("liveness_capture_count", "-1", "整数计数"),
    ("copy_ms", "NaN", "非法时长"),
    ("scrfd_inference_ms", "Infinity", "非法时长"),
    ("scrfd_postprocess_ms", "-1", "非法时长"),
    ("sample_ms", "", "分阶段耗时"),
    ("analysis_cpu_ms", "NaN", "非法时长"),
    ("preprocess_ms", "11", "超过 onFrame"),
    ("feature_ms", "0", "capture 次数"),
    ("liveness_ms", "0", "capture 次数"),
])
def test_v3_invalid_frame_metrics_fail_closed(column, value, fragment):
    data = timed_run()
    data[0][column] = value
    refresh(data)
    assert not data.audit["eligible"]
    assert any(fragment in issue for issue in data.audit["issues"]), data.audit["issues"]


@pytest.mark.parametrize("column,value", [("face_count", "1"), ("box_left", "10"), ("live_status", "1"),
                                         ("live_score", "0.9"), ("feature_ok", "1"), ("copy_ms", "0"),
                                         ("full_calls", "0"), ("preprocess_ms", "0")])
def test_v3_rejected_frames_cannot_reuse_previous_snapshot(column, value):
    data = timed_run([timed_row(), timed_row(1, state="REJECTED", cost="0.01")])
    assert data.audit["eligible"], data.audit["issues"]
    data[1][column] = value
    assert not refresh(data).audit["eligible"]


@pytest.mark.parametrize("state", ["ABORTED", "ERROR"])
def test_v3_aborted_or_failed_detection_cannot_be_empty_success(state):
    data = timed_run()
    data[0].update(analysis_status=state, note="error: detector failed", live_status="-101")
    assert not refresh(data).audit["eligible"]
    result = reporter.summarize_session(data, "xxr", [])
    assert result["identity_result"] == "error"
    assert result["attack_observation"] == "error"
    assert result["analysis"]["successful_admitted_onframe"]["count"] == 1
    assert result["analysis"]["all_call_wall_ms"] == 20


@pytest.mark.parametrize("change", [
    lambda d: d.metadata.update(timingSchemaVersion=True),
    lambda d: d.frame_columns.pop(),
    lambda d: d.metadata["framesColumns"].reverse(),
    lambda d: d.metadata.update(profileSha256="0" * 64),
    lambda d: d[0].pop("warmup"),
    lambda d: d[0].update(extra=""),
    lambda d: d[0].update(copy_ms=None),
    lambda d: d.metadata["actualBackendClips"][0]["metrics"]["analysisCounts"].update(ANALYZED=1),
    lambda d: d.metadata["actualBackendClips"][0]["metrics"]["analysisCounts"].update(ERROR=False),
    lambda d: d.metadata["actualBackendClips"][0]["metrics"].update(featureCaptureCount=1),
    lambda d: d.metadata["actualBackendClips"][0]["metrics"].update(livenessCaptureCount=1),
    lambda d: d.metadata["actualBackendClips"][0]["metrics"].update(timingOverflow=1),
    lambda d: d.metadata["actualBackendClips"][0].update(droppedFrames=False),
])
def test_v3_metadata_headers_and_conservation_fail_closed(change):
    data = timed_run()
    change(data)
    assert not refresh(data).audit["eligible"]


def test_v3_self_consistent_but_unknown_profile_is_rejected():
    data = timed_run()
    data.metadata["analysisProfile"]["detectorThreads"] = 1
    data.metadata["profileSha256"] = reporter.fingerprint(data.metadata["analysisProfile"])
    complete = data.metadata["actualBackendClips"][0]
    complete["effectiveConfig"]["detector"]["detectorThreads"] = 1
    complete["effectiveConfigSha256"] = reporter.fingerprint(complete["effectiveConfig"])
    assert not refresh(data).audit["eligible"]
    assert any("已知配置" in issue for issue in data.audit["issues"])


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_v3_nonfinite_json_even_in_optional_metric_is_ineligible(bad):
    data = timed_run()
    data.metadata["actualBackendClips"][0]["metrics"]["optional"] = bad
    assert not refresh(data).audit["eligible"]
    assert any("数值非法" in issue for issue in data.audit["issues"])


def test_v3_zero_is_allowed_only_for_measured_duration_and_cpu_can_be_absent():
    data = timed_run()
    data[0].update(copy_ms="0", analysis_cpu_ms="")
    assert refresh(data).audit["eligible"], data.audit["issues"]
    analysis = reporter.summarize_analysis(data)
    assert analysis["stages"]["copy_ms"]["count"] == 2
    assert analysis["stages"]["analysis_cpu_ms"]["count"] == 1


def test_v3_many_roi_calls_are_one_analyzed_frame():
    data = timed_run()
    data[1].update(roi_calls="3", sample_ms="3", scrfd_preprocess_ms="3", scrfd_inference_ms="6",
                   scrfd_postprocess_ms="3", roi_ms="18", copy_ms="1", preprocess_ms="19", detect_ms="22")
    assert refresh(data).audit["eligible"], data.audit["issues"]
    analysis = reporter.summarize_analysis(data)
    assert analysis["modes"]["ROI"]["frames"] == 1
    assert analysis["modes"]["ROI"]["onframe"]["mean_ms"] == 22
    assert analysis["stage_values_ms"]["roi_ms"] == [18.]
    assert analysis["stage_values_ms"]["scrfd_inference_ms"] == [2., 6.]


def test_v3_rejects_mixed_version_comparison_without_breaking_v2():
    old, new = versioned_run(), timed_run()
    assert old.audit["eligible"] and new.audit["eligible"]
    assert reporter.compare_run_evidence(old, old) == []
    assert reporter.compare_run_evidence(new, new) == []
    assert any("相同版本" in issue for issue in reporter.compare_run_evidence(old, new))
    assert reporter.summarize_session(old, "xxr", old.events)["analysis"] is None


def test_v3_changes_to_observation_level_prevent_strict_comparison():
    a, b = timed_run(), timed_run()
    b.metadata["timingSchemaVersion"] = 2
    assert any("timingSchemaVersion" in issue for issue in reporter.compare_run_evidence(a, b))


def test_v3_csv_round_trip_checks_actual_header(tmp_path):
    import csv
    data = timed_run()
    for filename, value in (("run.json", data.metadata), ("clip-completion.json", data.completions)):
        (tmp_path / filename).write_text(json.dumps(value), encoding="utf-8")
    for filename, values, fields in (("frames.csv", data, reporter.FRAME_COLUMNS_V3),
                                     ("events.csv", data.events, list(data.events[0])),
                                     ("enroll.csv", data.enrollments, list(data.enrollments[0]))):
        with (tmp_path / filename).open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)
    loaded, warnings = reporter.read_run(tmp_path)
    assert loaded.audit["eligible"], warnings
    assert loaded.frame_columns == reporter.FRAME_COLUMNS_V3


@pytest.mark.parametrize("state", ["REJECTED", "DROPPED"])
def test_v3_self_session_without_analyzed_frames_is_not_a_performance_sample(state):
    data = timed_run([timed_row(i, state=state) for i in range(2)])
    assert not data.audit["eligible"]
    assert any("没有任何 ANALYZED" in issue for issue in data.audit["issues"])


def test_v3_first_analyzed_frame_must_be_full_even_after_skips():
    data = timed_run([timed_row(state="DROPPED"), timed_row(1, state="REJECTED"), timed_row(2, mode="ROI")])
    assert not data.audit["eligible"]
    assert any("首次分析必须为 FULL" in issue for issue in data.audit["issues"])


def test_v3_full_scan_interval_counts_analyzed_frames_not_roi_calls_or_skips():
    frames = [timed_row()]
    for _ in range(9):
        frames.append(timed_row(len(frames), state="REJECTED", cost="0.001"))
        frames.append(timed_row(len(frames), state="DROPPED"))
        roi = timed_row(len(frames), mode="ROI")
        roi["roi_calls"] = "2"
        frames.append(roi)
    frames.append(timed_row(len(frames)))
    data = timed_run(frames)
    assert data.audit["eligible"], data.audit["issues"]
    too_late = timed_run([timed_row()] + [timed_row(i, mode="ROI") for i in range(1, 11)])
    assert not too_late.audit["eligible"]
    assert any("fullScanEvery" in issue for issue in too_late.audit["issues"])


@pytest.mark.parametrize("changes,parent", [
    ({"full_ms": "8.5"}, "preprocess_ms"),
    ({"preprocess_ms": "7.5"}, "preprocess_ms"),
    ({"sample_ms": "4"}, "full_ms"),
    ({"feature_capture_count": "1", "feature_ms": "3"}, "detect_ms"),
    ({"liveness_capture_count": "1", "liveness_ms": "3"}, "detect_ms"),
])
def test_v3_nonoverlapping_stages_must_fit_their_parent_interval(changes, parent):
    first = timed_row()
    first.update(changes)
    data = timed_run([first, timed_row(1, mode="ROI")])
    assert not data.audit["eligible"]
    assert any("不重叠分项之和超过父区间：" + parent in issue for issue in data.audit["issues"])


def test_v3_detector_and_capture_totals_allow_valid_nested_intervals():
    first = timed_row()
    first.update(feature_capture_count="2", feature_ms="1", liveness_capture_count="1", liveness_ms="0.5")
    data = timed_run([first, timed_row(1, mode="ROI")])
    assert data.audit["eligible"], data.audit["issues"]


@pytest.mark.parametrize("changes", [
    {"note": "error: detector failed"}, {"live_status": "-101"}, {"detector_status": "UNAVAILABLE"},
])
def test_v3_report_uses_the_same_successful_admission_denominator_everywhere(changes):
    data = timed_run()
    data[1].update(changes)
    assert not refresh(data).audit["eligible"]
    report = reporter.build_report(data)
    analysis = report["results"]["session_cascade"]["xxr_live"]["analysis"]
    assert analysis["status_counts"]["ANALYZED"] == 2  # Raw evidence stays intact.
    assert analysis["successful_admitted_count"] == 1
    assert analysis["modes"]["FULL"]["fraction_of_analyzed"] == 1
    assert analysis["modes"]["ROI"]["frames"] == 0
    assert analysis["all_call_wall_ms"] == 20
    text = reporter.render_report(report)
    assert "| 1/1 | 100.0% |" in text
    assert "| session_cascade/xxr_live | 1 |" in text


def test_v3_unmeasured_arcsoft_is_not_given_native_timing():
    data = timed_run()
    data.metadata["config"]["backends"] = ["session_arcsoft"]
    data.metadata["configSha256"] = reporter.fingerprint(data.metadata["config"])
    data.metadata["plannedBackends"] = ["session_arcsoft"]
    data.metadata["plannedBackendClips"][0]["backend"] = "session_arcsoft"
    data.metadata["actualEnrolledIds"] = {"session_arcsoft": ["xxr"]}
    complete = data.metadata["actualBackendClips"][0]
    complete.update(backend="session_arcsoft", effectiveConfig={"compareModel": "LIFE_PHOTO", "sdkVersion": "test-fixture"}, metrics={})
    complete["effectiveConfigSha256"] = reporter.fingerprint(complete["effectiveConfig"])
    data.events[0]["backend"] = "session_arcsoft"
    data.enrollments[0]["backend"] = "session_arcsoft"
    data[:] = [dict(timed_row(i, state="UNMEASURED"), backend="session_arcsoft") for i in range(2)]
    assert refresh(data).audit["eligible"], data.audit["issues"]
    assert reporter.summarize_session(data, "xxr", data.events)["analysis"] is None
    data[0]["copy_ms"] = "0"
    assert not refresh(data).audit["eligible"]
