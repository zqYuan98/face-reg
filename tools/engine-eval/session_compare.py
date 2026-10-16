"""Summarise measured session replay, never estimate ArcSoft timing from per-frame calls.

Only session_* rows are consumed. detect_ms is analysis-thread onFrame cost, not the sum of
background inference costs. Schema v2 decisions come from events.csv, including bounded tail
completion; legacy recognized notes remain readable. Elapsed time starts at the first replay frame.

    python session_compare.py <run_dir> [--subject xxr] [--json <output.json>]
    python session_compare.py <run_dir> --arcsoft-run <measured_session_arcsoft_run>

Strict integrity eligibility requires versioned run/manifest/events/completion evidence. Legacy
runs remain observation-only and may lose tail events. A missing decision is NOT proof of attack
rejection, and negative observations are not permanent verdicts. Always check actual identity.
"""
import argparse
import csv
import json
import hashlib
import math
import statistics
from collections import defaultdict
from pathlib import Path


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    rank = q * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


class RunRows(list):
    """Old (rows, warnings) API plus versioned audit evidence."""
    def __init__(self, rows, metadata=None, events=None, completions=None, enrollments=None):
        super().__init__(rows)
        self.metadata = metadata or {}
        self.events = events
        self.completions = completions
        self.enrollments = enrollments
        self.audit = audit_run(self)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def read_run(directory):
    directory = Path(directory)
    def read_json(name):
        path = directory / name
        if not path.is_file():
            return None
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    def read_csv(name):
        path = directory / name
        if not path.is_file():
            return None
        with path.open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source))
    rows = RunRows(read_csv("frames.csv") or [], read_json("run.json"), read_csv("events.csv"),
                   read_json("clip-completion.json"), read_csv("enroll.csv"))
    return rows, [directory.name + ": " + issue for issue in rows.audit["issues"]]


def audit_run(rows):
    """Fail closed for comparison eligibility; incomplete and legacy data remain observable."""
    meta, issues = rows.metadata, []
    version = meta.get("schemaVersion")
    if version != 2:
        issues.append("旧版或缺少 run.json schema v2：可能尾部截断，仅允许观察报告，不属于严格验收。")
    if meta.get("cancelled"):
        issues.append("运行被取消，不能视为完整测试。")
    if meta.get("failures"):
        issues.append("存在后端失败，请检查 run.json 的 failures。")
    if meta.get("config", {}).get("arcSoftIdCardModel"):
        issues.append("虹软使用 ID_CARD；普通注册照应使用 LIFE_PHOTO，不能混为相同配置。")
    if version != 2:
        return {"schema_version": version, "eligible": False, "issues": issues, "legacy": True}
    if meta.get("finished") is not True or not meta.get("finishedAtEpochMs") or meta.get("complete") is not True:
        issues.append("run 未完整 finished/complete。")
    if meta.get("cancelled") is not False:
        issues.append("缺少明确的未取消标记。")
    config = meta.get("config", {})
    if config.get("arcSoftIdCardModel") is not False or meta.get("lifePhotoChecked") is not True:
        issues.append("LIFE_PHOTO 检查缺失或不通过。")
    if config.get("evaluationVariant") not in ("baseline", "shared_capture") or number(config.get("finishTimeoutMs")) is None:
        issues.append("有效 variant/drain 配置缺失。")
    if meta.get("configSha256") != fingerprint(config):
        issues.append("config 指纹不匹配。")
    manifest = meta.get("manifest", {})
    audit_manifest(manifest, issues)
    planned = meta.get("plannedBackendClips", [])
    expected = {(entry.get("backend"), entry.get("clipId")): entry for entry in planned}
    backends = meta.get("plannedBackends", [])
    if not expected or len(expected) != len(planned) or set(backends) != set(config.get("backends", [])):
        issues.append("planned backend/clip 覆盖非法或缺失。")
    planned_clips = {clip for _, clip in expected}
    if set(expected) != {(backend, clip) for backend in backends for clip in planned_clips}:
        issues.append("planned backend/clip 不是完整笛卡尔覆盖。")
    audit_models_and_enrollment(rows, backends, manifest, issues)
    frames = defaultdict(list)
    for row in rows:
        frames[(row.get("backend"), row.get("clip_id"))].append(row)
    if set(frames) != set(expected):
        issues.append("实际帧 backend/clip 覆盖与 planned 不一致（包括完全缺失后端）。")
    completion_doc = rows.completions or {}
    completion_list = completion_doc.get("clips", [])
    completions = {(entry.get("backend"), entry.get("clipId")): entry for entry in completion_list}
    if completion_doc.get("schemaVersion") != 1 or len(completions) != len(completion_list) or set(completions) != set(expected):
        issues.append("clip-completion 版本/覆盖不完整。")
    if meta.get("actualBackendClips") != completion_list:
        issues.append("run 与 clip-completion 完成记录不一致。")
    event_groups = defaultdict(list)
    if rows.events is None or meta.get("eventSchemaVersion") != 1:
        issues.append("缺少结构 events schema v1。")
    for event in rows.events or []:
        key = (event.get("backend"), event.get("clip_id"))
        event_groups[key].append(event)
        if key not in expected:
            issues.append("事件来自未计划的 backend/clip。")
    for key, plan in expected.items():
        audit_clip(key, plan, frames.get(key, []), completions.get(key, {}), event_groups.get(key, []), config, issues)
    return {"schema_version": version, "eligible": not issues, "issues": list(dict.fromkeys(issues)), "legacy": False}


def audit_manifest(manifest, issues):
    if manifest.get("schemaVersion") != 1:
        issues.append("hash manifest 版本缺失。")
    for group in ("apks", "inputs", "enrollment", "modelAssets", "nativeLibraries"):
        values = manifest.get(group)
        if not isinstance(values, dict) or not values:
            issues.append(group + " 指纹清单缺失或为空。")
            continue
        for name, entry in values.items():
            digest = entry.get("sha256", "") if isinstance(entry, dict) else ""
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or number(entry.get("bytes")) is None or entry["bytes"] <= 0:
                issues.append(group + "/" + name + " 指纹非法。")
        if group in ("inputs", "enrollment", "modelAssets") and manifest.get(group + "Sha256") != fingerprint(values):
            issues.append(group + " 聚合指纹不匹配。")


def audit_models_and_enrollment(rows, backends, manifest, issues):
    required_models = set()
    for backend in backends:
        if backend.startswith("session_") and backend != "session_arcsoft":
            required_models.update(("insightface/det_500m.onnx", "live/config.json", "live/model_1.bin",
                                   "live/model_1.param", "live/model_2.bin", "live/model_2.param"))
            if backend in ("session_mbf", "session_cascade"):
                required_models.add("insightface/w600k_mbf.onnx")
            if backend in ("session_r50_int8", "session_cascade"):
                required_models.add("insightface/w600k_r50_int8.onnx")
    missing = required_models - set(manifest.get("modelAssets", {}))
    if missing:
        issues.append("缺少后端所需模型资产 SHA：" + ", ".join(sorted(missing)))
    expected_ids = manifest.get("expectedEnrolledIds", [])
    if not expected_ids or len(set(expected_ids)) != len(expected_ids):
        issues.append("预期注册身份为空或重复。")
    actual_ids = rows.metadata.get("actualEnrolledIds", {})
    for backend in backends:
        if sorted(actual_ids.get(backend, [])) != sorted(expected_ids):
            issues.append(backend + " 实际入库身份不完整。")
        enrolled = [entry for entry in (rows.enrollments or []) if entry.get("backend") == backend]
        if sorted(entry.get("enrolled_id") for entry in enrolled if entry.get("ok") == "1") != sorted(expected_ids) or any(entry.get("ok") != "1" for entry in enrolled):
            issues.append(backend + " enroll.csv 注册失败/缺失。")


def audit_clip(key, plan, data, complete, events, config, issues):
    name = "/".join(key)
    try:
        indices = [int(row["frame_index"]) for row in data]
        timestamps = [int(row["timestamp_ns"]) for row in data]
        if indices != list(range(plan["frameCount"])) or any(b <= a for a, b in zip(timestamps, timestamps[1:])):
            issues.append(name + " 帧缺失/重复/乱序或时间戳非单调。")
        if timestamps and abs((timestamps[-1] - timestamps[0]) / 1e6 - plan["durationMs"]) >= 1:
            issues.append(name + " 录制时窗与帧时间戳不一致。")
    except (KeyError, ValueError, TypeError):
        issues.append(name + " 非法帧索引/时间戳。")
    if any(row.get("subject_id") != plan.get("subjectId") or row.get("label") != plan.get("label")
           or row.get("attack_type", "") != plan.get("attackType", "") for row in data):
        issues.append(name + " 标签与 planned 不符。")
    frame_errors = sum(row.get("live_status") == "-101" or row.get("note", "").startswith("error:") for row in data)
    if frame_errors or complete.get("frameErrors") != frame_errors:
        issues.append(name + " 存在逐帧错误或错误计数不符。")
    if not complete.get("eof") or complete.get("cancelled") is not False or complete.get("stopReason") != "eof" or complete.get("error") or complete.get("drainOk") is not True or complete.get("drainError"):
        issues.append(name + " 未正常EOF/取消/异常/drain失败。")
    if complete.get("receivedFrames") != len(data) or complete.get("eventOverflow", 0) or complete.get("timestampErrors", 0):
        issues.append(name + " received计数不符/队列溢出/时间戳错误。")
    effective = complete.get("effectiveConfig")
    if not isinstance(effective, dict) and key[0].startswith("session_"):
        issues.append(name + " 缺少结构 effectiveConfig。")
    if effective is None or complete.get("effectiveConfigSha256") != fingerprint(effective):
        issues.append(name + " effectiveConfig 指纹不匹配。")
    if key[0] == "session_arcsoft" and (not isinstance(effective, dict) or effective.get("compareModel") != "LIFE_PHOTO" or effective.get("sdkVersion") in (None, "unknown", "")):
        issues.append(name + " ArcSoft SDK版本/LIFE_PHOTO未确认。")
    if key[0] in ("session_cascade", "session_mbf", "session_r50_int8") and isinstance(effective, dict):
        if effective.get("session", {}).get("shareFeatureCapture") is not (config.get("evaluationVariant") == "shared_capture"):
            issues.append(name + " 实际capture开关与variant不一致。")
    if complete.get("eventCount") != len(events):
        issues.append(name + " 事件数量不符。")
    event_errors = 0
    for sequence, event in enumerate(events, 1):
        try:
            if int(event.get("schema_version", -1)) != 1 or int(event["epoch"]) != complete.get("epoch") or int(event["sequence"]) != sequence:
                raise ValueError("schema/epoch/sequence")
            elapsed = number(event.get("elapsed_ms"))
            if elapsed is None or elapsed < 0 or event.get("expected_identity") != plan.get("subjectId"):
                raise ValueError("time/identity")
            if event.get("window") != ("recording" if elapsed <= plan["durationMs"] else "tail"):
                raise ValueError("window")
            if event.get("kind") not in ("recognized", "liveness", "recognition_failed", "error"):
                raise ValueError("kind")
            if event["kind"] == "recognized" and (not event.get("actual_identity") or number(event.get("score")) is None):
                raise ValueError("recognition")
            event_errors += event["kind"] == "error"
        except (ValueError, TypeError, KeyError):
            issues.append(name + " 结构事件schema/顺序/时窗/身份非法。")
    if event_errors or complete.get("eventErrors", 0) != event_errors:
        issues.append(name + " worker错误或错误计数不符。")


def clip_signature(rows):
    """Cross-run comparisons require identical recorded frame identifiers/timestamps and labels."""
    return sorted((row["frame_index"], row["timestamp_ns"], row["subject_id"], row["label"],
                   row.get("attack_type", "")) for row in rows)


def parse_event(note):
    if not note.startswith("recognized:"):
        return None
    parts = note.split(":")
    if len(parts) != 4 or not parts[1]:
        raise ValueError("Malformed recognition event")
    score, elapsed = number(parts[2]), number(parts[3])
    if score is None or elapsed is None or elapsed < 0:
        raise ValueError("Invalid recognition score or time")
    return {"recognized_id": parts[1], "score": score, "elapsed_ms": elapsed}


def summarize_session(rows, subject_id, structured_events=None):
    done = [row for row in rows if row.get("note", "") != "dropped"]
    costs, events = [], []
    errors = malformed_events = missing_timings = 0
    alive = not_alive = saw_face = False
    for row in sorted(done, key=lambda item: int(item["frame_index"])):
        note = row.get("note", "")
        error = note.startswith("error:") or row.get("live_status") == "-101"
        errors += int(error)
        cost = number(row.get("detect_ms"))
        if cost is not None and cost >= 0 and not error:
            costs.append(cost)
        else:
            missing_timings += 1
        face_count = number(row.get("face_count"))
        saw_face |= face_count is not None and face_count > 0
        alive |= row.get("live_status") == "1"
        not_alive |= row.get("live_status") == "0"
        try:
            event = parse_event(note) if structured_events is None else None
        except ValueError:
            malformed_events += 1
            continue
        if event is not None:
            events.append(event)

    if structured_events is not None:
        for event in structured_events:
            live = event.get("live_status")
            alive |= live == "1"
            not_alive |= live == "0"
            saw_face |= number(event.get("track_id")) is not None and number(event.get("track_id")) >= 0
            errors += event.get("kind") == "error"
            if event.get("kind") != "recognized":
                continue
            elapsed, score = number(event.get("elapsed_ms")), number(event.get("score"))
            if elapsed is None or elapsed < 0 or score is None or not event.get("actual_identity"):
                malformed_events += 1
                continue
            events.append({"recognized_id": event["actual_identity"], "score": score, "elapsed_ms": elapsed,
                           "window": event.get("window"), "epoch": event.get("epoch"),
                           "sequence": event.get("sequence"), "track_id": event.get("track_id")})
    correct = [event for event in events if event["recognized_id"] == subject_id]
    wrong = [event for event in events if event["recognized_id"] != subject_id]
    if not rows and not events:
        identity = "missing"
    elif wrong:
        identity = "wrong_identity"  # Never hide an earlier wrong acceptance behind a later success.
    elif correct:
        identity = "correct"
    elif errors or malformed_events:
        identity = "error"
    else:
        identity = "no_decision"

    if not rows and not events:
        attack = "missing"
    elif events:
        attack = "authentication_accepted"
    elif alive:
        attack = "liveness_passed_only"
    elif errors or malformed_events:
        attack = "error"
    elif not_alive:
        attack = "not_alive_observed"
    elif not saw_face:
        attack = "no_face_observed"
    else:
        attack = "undecided"

    return {
        "total_frames": len(rows), "processed_rows": len(done),
        "dropped_frames": len(rows) - len(done), "costs_ms": costs,
        "missing_or_invalid_timings": missing_timings, "error_rows": errors,
        "malformed_events": malformed_events, "identity_result": identity,
        "correct_elapsed_ms": min((event["elapsed_ms"] for event in correct), default=None)
        if identity == "correct" else None,
        "events": events, "wrong_identity_events": len(wrong),
        "liveness_alive_observed": alive, "liveness_not_alive_observed": not_alive,
        "attack_observation": attack,
        "event_source": "events.csv" if structured_events is not None else "legacy_frame_notes",
        "recording_events": [event for event in events if event.get("window") == "recording"],
        "tail_events": [event for event in events if event.get("window") == "tail"],
        "correct_recording_elapsed_ms": min((event["elapsed_ms"] for event in correct if event.get("window") == "recording"), default=None),
        "correct_tail_elapsed_ms": min((event["elapsed_ms"] for event in correct if event.get("window") == "tail"), default=None),
    }


def build_report(rows, subject=None, arcsoft_rows=None):
    frames = defaultdict(list)
    metadata = {}
    for row in rows:
        if subject is not None and row["subject_id"] != subject:
            continue
        if not row["backend"].startswith("session_"):
            continue
        clip = row["clip_id"]
        info = {"subject_id": row["subject_id"], "label": row["label"],
                "attack_type": row.get("attack_type", "")}
        if clip in metadata and metadata[clip] != info:
            raise ValueError(f"Conflicting labels in clip {clip}")
        metadata[clip] = info
        frames[(row["backend"], clip)].append(row)
    planned_backends = set()
    for entry in getattr(rows, "metadata", {}).get("plannedBackendClips", []):
        if not entry.get("backend", "").startswith("session_") or (subject is not None and entry.get("subjectId") != subject):
            continue
        planned_backends.add(entry["backend"])
        info = {"subject_id": entry["subjectId"], "label": entry["label"], "attack_type": entry.get("attackType", "")}
        if entry["clipId"] in metadata and metadata[entry["clipId"]] != info:
            raise ValueError("planned片段标签与frames不一致")
        metadata[entry["clipId"]] = info
    if not frames and not planned_backends:
        raise ValueError("没有匹配的 session_* 实测数据；普通 arcsoft 逐帧数据不能替代会话实测。")

    if arcsoft_rows is not None:
        reference = defaultdict(list)
        for row in arcsoft_rows:
            if row["backend"] == "session_arcsoft" and row["clip_id"] in metadata:
                reference[row["clip_id"]].append(row)
        if not reference:
            raise ValueError("对照目录没有 session_arcsoft 实测行；不再生成模拟虹软结果。")
        for clip in metadata:
            if clip not in reference:
                raise ValueError(f"对照缺少片段 {clip}")
            base = next(value for (backend, key), value in frames.items() if key == clip)
            if clip_signature(base) != clip_signature(reference[clip]):
                raise ValueError(f"对照的帧时间戳、身份或标签不匹配：{clip}")
            frames[("session_arcsoft", clip)] = reference[clip]

    backends = sorted({backend for backend, _ in frames} | planned_backends)
    results, warnings = {}, []
    audits = [getattr(rows, "audit", {"eligible": False, "legacy": True, "issues": ["缺少run证据，仅允许观察报告。"]})]
    if arcsoft_rows is not None:
        audits.append(getattr(arcsoft_rows, "audit", {"eligible": False, "legacy": True, "issues": ["对照缺少run证据。"]}))
        warnings.extend(compare_run_evidence(rows, arcsoft_rows))
    event_groups = defaultdict(list)
    for source in (rows, arcsoft_rows):
        if source is None:
            continue
        for event in getattr(source, "events", None) or []:
            if source is arcsoft_rows and event.get("backend") != "session_arcsoft":
                continue
            if source is rows and arcsoft_rows is not None and event.get("backend") == "session_arcsoft":
                continue
            event_groups[(event.get("backend"), event.get("clip_id"))].append(event)
    for backend in backends:
        per_clip = {}
        for clip, info in sorted(metadata.items()):
            data = frames.get((backend, clip), [])
            if not data:
                warnings.append(f"{backend}: 缺少 {clip}，未按成功或拦截计数。")
            source = arcsoft_rows if backend == "session_arcsoft" and arcsoft_rows is not None else rows
            structured = event_groups.get((backend, clip), []) if getattr(source, "events", None) is not None else None
            per_clip[clip] = summarize_session(data, info["subject_id"], structured)
            doc = getattr(source, "completions", None) or {}
            per_clip[clip]["completion"] = next((entry for entry in doc.get("clips", [])
                                                if entry.get("backend") == backend and entry.get("clipId") == clip), None)
        results[backend] = per_clip
    comparison_eligible = all(audit["eligible"] for audit in audits) and not warnings
    warnings.extend(issue for audit in audits for issue in audit["issues"])
    return {"subject_filter": subject, "clips": metadata, "results": results, "warnings": list(dict.fromkeys(warnings)),
            "audit": {"eligible": comparison_eligible, "scope": "integrity_not_security_acceptance", "runs": audits},
            "scope": "measured_sessions_only",
            "latency_origin": "first_replay_frame_to_recorded_worker_decision",
            "analysis_cost_scope": "onFrame_call_including_idle_calls_and_warmup_excluding_background_inference"}


def compare_run_evidence(left, right):
    """Different products are allowed; inputs/models/enrollment/harness must still match."""
    a, b = getattr(left, "metadata", {}), getattr(right, "metadata", {})
    if a.get("schemaVersion") != 2 or b.get("schemaVersion") != 2:
        return ["跨run缺少v2证据，只作观察比较，不宣称严格配置一致。"]
    issues = []
    for group in ("inputs", "enrollment", "modelAssets", "apks", "nativeLibraries"):
        if a.get("manifest", {}).get(group) != b.get("manifest", {}).get(group):
            issues.append("跨run " + group + " 指纹不匹配。")
    config_a = {key: value for key, value in a.get("config", {}).items() if key != "backends"}
    config_b = {key: value for key, value in b.get("config", {}).items() if key != "backends"}
    if config_a != config_b:
        issues.append("跨run有效回放配置不匹配。")
    ca = {(c.get("backend"), c.get("clipId")): c for c in a.get("actualBackendClips", [])}
    cb = {(c.get("backend"), c.get("clipId")): c for c in b.get("actualBackendClips", [])}
    for key in ca.keys() & cb.keys():
        if ca[key].get("effectiveConfigSha256") != cb[key].get("effectiveConfigSha256"):
            issues.append("跨run同后端effectiveConfig不匹配：" + "/".join(key))
    return issues


def display_number(value):
    return "—" if value is None else f"{value:.1f}"


def safe_text(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_report(report):
    lines = ["## 会话回放实测\n", "只统计 session_*，不使用串行模拟的虹软数据。"]
    lines.append("完整性审计：" + ("通过（不等于安全/精度验收）" if report.get("audit", {}).get("eligible")
                                  else "未通过；仅供观察，不能宣称严格验收。"))
    for warning in report["warnings"]:
        lines.append("- 警告：" + safe_text(warning))
    lines += ["\n### 分析线程调用成本（含预热、含空闲跳过检测的调用）\n",
              "不含后台活体/识别计算；不是整条流水线的单帧耗时。",
              "| 方案 | 平均 ms | P50 | P95 | 有效计时/调用 | 丢帧/输入 |",
              "|---|---|---|---|---|---|"]
    for name, clips in report["results"].items():
        costs = [cost for clip in clips.values() for cost in clip["costs_ms"]]
        calls = sum(clip["processed_rows"] for clip in clips.values())
        total = sum(clip["total_frames"] for clip in clips.values())
        drop = sum(clip["dropped_frames"] for clip in clips.values())
        values = (statistics.mean(costs) if costs else None, percentile(costs, .5), percentile(costs, .95))
        lines.append(f"| {name} | " + " | ".join(display_number(value) for value in values)
                     + f" | {len(costs)}/{calls} | {drop}/{total} |")

    attack_names = {
        "authentication_accepted": "**认证放行**", "liveness_passed_only": "**活体通过，未观察到认证**",
        "not_alive_observed": "观察到非活体（未观察到放行）", "no_face_observed": "未观察到人脸；不算活体拒绝",
        "undecided": "未决；不算活体拒绝", "error": "错误；不算活体拒绝", "missing": "缺数据",
    }
    lines += ["\n### 逐片段结果\n", "| 片段 | 本人/标签 | " + " | ".join(report["results"]) + " |",
              "|---|---|" + "---|" * len(report["results"])]
    for clip_id, info in sorted(report["clips"].items()):
        cells = []
        for clips in report["results"].values():
            result = clips[clip_id]
            if info["label"] == "live":
                status = result["identity_result"]
                if status == "correct":
                    suffix = "（片尾）" if result.get("correct_recording_elapsed_ms") is None and result.get("correct_tail_elapsed_ms") is not None else ""
                    cells.append(f"正确识别 {display_number(result['correct_elapsed_ms'])} ms{suffix}")
                elif status == "wrong_identity":
                    wrong = sorted({event["recognized_id"] for event in result["events"]
                                    if event["recognized_id"] != info["subject_id"]})
                    cells.append("**错认：" + safe_text(", ".join(wrong)) + "**")
                else:
                    cells.append({"error": "错误/未决", "missing": "缺数据", "no_decision": "未观察到识别结果"}[status])
            else:
                cells.append(attack_names[result["attack_observation"]])
        label = info["label"] + ("/" + info["attack_type"] if info["attack_type"] else "")
        lines.append(f"| {safe_text(clip_id)} | {safe_text(info['subject_id'])} / {safe_text(label)} | "
                     + " | ".join(cells) + " |")

    lines += ["\n### 汇总\n",
              "| 方案 | 真人正确/总数 | 真人错认 | 真人未决/缺失/错误 | 正确片段决策中位 ms | 攻击认证放行 | 仅活体通过 | 观察到非活体 | 攻击未决/缺失/错误 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, clips in report["results"].items():
        live = [result for clip, result in clips.items() if report["clips"][clip]["label"] == "live"]
        attack = [result for clip, result in clips.items() if report["clips"][clip]["label"] == "attack"]
        times = [result["correct_elapsed_ms"] for result in live if result["identity_result"] == "correct"]
        wrong = sum(result["identity_result"] == "wrong_identity" for result in live)
        auth = sum(result["attack_observation"] == "authentication_accepted" for result in attack)
        alive = sum(result["attack_observation"] == "liveness_passed_only" for result in attack)
        negative = sum(result["attack_observation"] == "not_alive_observed" for result in attack)
        uncertain = len(attack) - auth - alive - negative
        lines.append(f"| {name} | {len(times)}/{len(live)} | {wrong} | {len(live) - len(times) - wrong} | "
                     + display_number(statistics.median(times) if times else None)
                     + f" | {auth}/{len(attack)} | {alive} | {negative} | {uncertain} |")
    lines += ["录制时窗与片尾认证事件（独立计数；legacy无时窗信息）", "| 方案 | 时窗内认证事件 | 片尾认证事件 |", "|---|---|---|"]
    for name, clips in report["results"].items():
        during = sum(len(c["recording_events"]) for c in clips.values())
        tail = sum(len(c["tail_events"]) for c in clips.values())
        lines.append(f"| {name} | {during} | {tail} |")
    lines += ["\n### 限制\n",
              "- 时延从回放首帧起算，不是启动相机或人进入画面的真实端到端时延；只对正确识别片段求中位数。",
              "- v2优先使用events.csv与片尾drain；旧版frame notes可能尾部截断，无结果不能判为可靠拒绝。片尾时间不截短，不补造活体票。",
              "- frames活体列仍是状态快照；v2事件记录有效票完成，但不包含完整推理诊断。未决/缺票/超时/错误均不算非活体拒绝。",
              "- 空闲调用、预热和后台 CPU 竞争影响平均值；不得把 session 的各个毫秒列相加当完整流水线时间。",
              "- 这是固定测试片段的观察统计，不能用少量片段证明低 FAR、活体安全性或普遍领先虹软。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--subject", help="只统计这个 subject_id，例如 xxr")
    parser.add_argument("--arcsoft-run", help="读取另一次运行中的 session_arcsoft 实测行（必须是相同录制帧）")
    parser.add_argument("--json", type=Path, help="可选：将可审计的分类结果保存为 JSON")
    args = parser.parse_args()
    rows, warnings = read_run(args.run)
    reference = None
    if args.arcsoft_run:
        reference, more_warnings = read_run(args.arcsoft_run)
        warnings.extend(more_warnings)
    try:
        report = build_report(rows, subject=args.subject, arcsoft_rows=reference)
    except ValueError as error:
        parser.error(str(error))
    report["warnings"] = list(dict.fromkeys(warnings + report["warnings"]))
    report["run"] = str(Path(args.run))
    print(render_report(report), end="")
    if args.json:
        with args.json.open("w", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")


if __name__ == "__main__":
    main()
