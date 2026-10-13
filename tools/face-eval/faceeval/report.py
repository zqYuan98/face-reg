"""Builds the parity report for one face-bench run.

Usage:
    python -m faceeval.report RUN_DIR [--ours legacy_fixed] [--ref arcsoft] [--out report.md]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from . import latency, liveness, recognition
from .data import Run, load_run

VERDICT_TEXT = {
    "PASS": "持平或更好",
    "WORSE": "不如对照",
    "INSUFFICIENT": "样本不足，未能证明持平",
    "N/A": "无法比较",
}
STAGE_TEXT = {
    "full_pipeline_ms": "单帧完整流程",
    "preprocess_ms": "预处理",
    "detect_ms": "检测",
    "liveness_ms": "活体",
    "feature_ms": "特征",
}
DEFAULT_LIVENESS_THRESHOLDS = {"arcsoft": 0.5, "legacy_asis": 0.7, "legacy_fixed": 0.7}


def _ms(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:.1f}"


def _pct(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:.2%}"


def _threshold(value: float) -> str:
    if math.isnan(value):
        return "—"
    return "-∞" if math.isinf(value) else f"{value:.4f}"


def build(run: Run, ours: str, ref: str, fars: list[float], rule: liveness.Rule, min_attacks: int) -> tuple[str, dict]:
    lines: list[str] = []
    summary: dict = {"run": str(run.path), "ours": ours, "ref": ref, "verdicts": {}}
    backends = run.backends()
    if ours not in backends or ref not in backends:
        raise SystemExit(f"run contains backends {backends}; need both {ours!r} and {ref!r}")

    device = run.meta.get("device", {})
    clips = {(r.clip_id, r.label, r.attack_type) for r in run.frames if r.backend == ref}
    by_kind = Counter("真人" if label == "live" else f"攻击/{attack}" for _, label, attack in clips)
    lines += [f"# 对标报告：{ours} 对比 {ref}", "",
              f"- 运行目录：`{run.path.name}`",
              f"- 设备：{device.get('manufacturer', '?')} {device.get('model', '?')}，"
              f"SoC {device.get('socModel', device.get('hardware', '?'))}，Android {device.get('androidRelease', '?')}",
              f"- 片段：{len(clips)} 个（" + "，".join(f"{k} {v}" for k, v in sorted(by_kind.items())) + "）"]
    for name, text in run.meta.get("backends", {}).items():
        lines.append(f"- `{name}`：{text}")
    for name, text in run.meta.get("failures", {}).items():
        lines.append(f"- **`{name}` 运行失败**：{text}")
    if run.meta.get("cancelled"):
        lines.append("- **本次运行被中途取消，结果不完整。**")
    lines.append("")

    # ---------------- liveness ----------------
    ref_default = liveness.default_point(run, ref, DEFAULT_LIVENESS_THRESHOLDS.get(ref, 0.5), rule)
    ours_points = liveness.sweep(run, ours, rule)
    live_section: list[str] = ["## 活体", ""]
    live_verdict = "INSUFFICIENT"
    if ref_default is None or not ours_points:
        live_section.append("缺少活体数据，无法比较。")
    else:
        chosen, matched = liveness.choose_matching(ours_points, ref_default.bpcer.value)
        type_verdicts = liveness.compare_types(chosen, ref_default, min_attacks)
        live_verdict = liveness.overall(list(type_verdicts.values())) if matched else "WORSE"
        live_section += [
            f"判定规则：一个片段内，最近 {rule.window} 次可判断的帧中有 {rule.required} 次判为活体即接受；"
            f"每类攻击至少 {min_attacks} 个片段才下结论。",
            "",
            f"- {ref} 默认阈值 {_threshold(ref_default.threshold)}：BPCER {ref_default.bpcer.fmt()}",
            f"- {ours} 取 BPCER 不高于 {ref} 时攻击放过率最低的阈值 {_threshold(chosen.threshold)}："
            f"BPCER {chosen.bpcer.fmt()}" + ("" if matched else "。**任何阈值都达不到对照的 BPCER**"),
            f"- 无法判断的真人片段：{ours} {chosen.undecided_bona}，{ref} {ref_default.undecided_bona}",
            "",
            f"| 攻击类型 | {ours} APCER | {ref} APCER | 判定 |",
            "|---|---|---|---|",
        ]
        for attack_type, verdict in type_verdicts.items():
            mine = chosen.apcer_by_type.get(attack_type)
            theirs = ref_default.apcer_by_type.get(attack_type)
            live_section.append(f"| {attack_type} | {mine.fmt() if mine else '—'} | {theirs.fmt() if theirs else '—'} | "
                                f"{VERDICT_TEXT[verdict]} |")
        kind_ours, clips_ours = liveness.clips_for(run, ours)
        kind_ref, clips_ref = liveness.clips_for(run, ref)
        fb_ours, fa_ours = liveness.frame_level(clips_ours, kind_ours, chosen.threshold)
        fb_ref, fa_ref = liveness.frame_level(clips_ref, kind_ref, ref_default.threshold)
        live_section += ["", "逐帧口径（每帧独立判断，帧间相关，只作参考）：",
                         f"- {ours}：BPCER {fb_ours.fmt()}；APCER {fa_ours.fmt()}",
                         f"- {ref}：BPCER {fb_ref.fmt()}；APCER {fa_ref.fmt()}", ""]
        live_section += [f"{ref} 阈值扫描（DET 曲线点）：", "", "| 阈值 | BPCER | 最差类型 APCER |", "|---|---|---|"]
        for point in liveness.sweep(run, ref, rule):
            live_section.append(f"| {_threshold(point.threshold)} | {_pct(point.bpcer.value)} | {_pct(point.apcer_max)} |")
        summary["liveness"] = {
            "ref_threshold": ref_default.threshold, "ours_threshold": chosen.threshold, "matched_bpcer": matched,
            "ref_bpcer": ref_default.bpcer.value, "ours_bpcer": chosen.bpcer.value,
            "apcer": {t: {"ours": chosen.apcer_by_type.get(t).value if chosen.apcer_by_type.get(t) else None,
                          "ref": ref_default.apcer_by_type.get(t).value if ref_default.apcer_by_type.get(t) else None,
                          "verdict": v} for t, v in type_verdicts.items()},
        }
    summary["verdicts"]["liveness"] = live_verdict

    # ---------------- recognition ----------------
    rec_section: list[str] = ["## 识别率", "",
                              "每家在自己的冒名分数上取阈值，再比相同误识率下的通过率。同一片段的多帧不独立，区间偏乐观。", "",
                              f"| 目标 FAR | {ours} TAR | {ref} TAR | {ours} 冒名对数 | {ref} 冒名对数 | 判定 |",
                              "|---|---|---|---|---|---|"]
    ours_split, ref_split = recognition.split(run, ours), recognition.split(run, ref)
    rec_verdicts = {}
    for far in fars:
        mine, theirs = recognition.at_far(ours_split, far), recognition.at_far(ref_split, far)
        verdict = recognition.compare(mine, theirs)
        rec_verdicts[far] = verdict
        rec_section.append(f"| {far:g} | {mine.tar.fmt()} | {theirs.tar.fmt()} | {mine.impostor_count} | "
                           f"{theirs.impostor_count} | {VERDICT_TEXT[verdict]} |")
    primary_far = fars[0]
    for name, data in ((ours, ours_split), (ref, ref_split)):
        point = recognition.at_far(data, primary_far)
        rank1, fpir = recognition.identification(data, point.threshold)
        rec_section.append(f"\n- {name} 在 FAR={primary_far:g} 阈值 {_threshold(point.threshold)} 下："
                           f"1:N 首位命中率 {rank1.fmt()}；非库内人员误识率 {fpir.fmt()}")
    rec_section.append("")
    summary["verdicts"]["recognition"] = rec_verdicts[primary_far]
    summary["recognition"] = {str(far): v for far, v in rec_verdicts.items()}

    # ---------------- latency ----------------
    lat_ours, lat_ref = latency.stage_stats(run, ours), latency.stage_stats(run, ref)
    lat_verdicts = latency.compare(lat_ours, lat_ref)
    lat_section = ["## 速度", "", "单位毫秒，已排除每个片段开头的预热帧。各家阶段划分不同：现有识别库的“特征”包含其内部检测、"
                   "关键点和姿态，所以“单帧完整流程”是最公平的对比项。", "",
                   f"| 阶段 | {ours} P50 | {ours} P95 | {ref} P50 | {ref} P95 | 判定 |", "|---|---|---|---|---|---|"]
    for stage in ("full_pipeline_ms",) + latency.STAGES:
        mine, theirs = lat_ours[stage], lat_ref[stage]
        verdict = lat_verdicts.get(stage, "N/A")
        lat_section.append(f"| {STAGE_TEXT[stage]} | {_ms(mine.p50)} | {_ms(mine.p95)} | {_ms(theirs.p50)} | "
                           f"{_ms(theirs.p95)} | {VERDICT_TEXT[verdict] if stage in latency.COMPARED else '—'} |")
    lat_section.append("")
    compared = [v for v in lat_verdicts.values() if v != "N/A"]
    summary["verdicts"]["speed"] = "WORSE" if "WORSE" in compared else ("PASS" if compared else "N/A")
    summary["speed"] = {stage: {"ours_p50": lat_ours[stage].p50, "ours_p95": lat_ours[stage].p95,
                                "ref_p50": lat_ref[stage].p50, "ref_p95": lat_ref[stage].p95,
                                "verdict": lat_verdicts.get(stage)} for stage in latency.COMPARED}

    # ---------------- other backends ----------------
    others = [b for b in backends if b not in (ours, ref)]
    other_section: list[str] = []
    if others:
        other_section = ["## 其他后端", "", "| 后端 | 单帧完整流程 P50 | 活体默认阈值 BPCER | 活体默认阈值最差 APCER | "
                         f"TAR@FAR={primary_far:g} |", "|---|---|---|---|---|"]
        for name in [ours, ref] + others:
            point = liveness.default_point(run, name, DEFAULT_LIVENESS_THRESHOLDS.get(name, 0.5), rule)
            far_point = recognition.at_far(recognition.split(run, name), primary_far)
            other_section.append(f"| {name} | {_ms(latency.stage_stats(run, name)['full_pipeline_ms'].p50)} | "
                                 f"{_pct(point.bpcer.value) if point else '—'} | {_pct(point.apcer_max) if point else '—'} | "
                                 f"{_pct(far_point.tar.value)} |")
        other_section.append("")

    head = ["## 结论", "", "| 维度 | 判定 |", "|---|---|",
            f"| 活体 | {VERDICT_TEXT[summary['verdicts']['liveness']]} |",
            f"| 识别率（FAR={primary_far:g}） | {VERDICT_TEXT[summary['verdicts']['recognition']]} |",
            f"| 速度 | {VERDICT_TEXT[summary['verdicts']['speed']]} |", ""]
    notes = ["## 说明", "",
             "- 回放只测各阶段耗时，不含端到端“进入画面到给出结果”的时延，后者需要实时模式。",
             f"- {ref} 的 RGB 活体只输出三态，曲线点来自逐个阈值重放。",
             "- `legacy_asis` 的人脸框在传感器方向坐标系下，和其他后端的框不可直接比较。",
             "- 判定只基于这一次运行的数据；换设备、换光照、换攻击介质都要重新测。", ""]
    return "\n".join(lines + head + live_section + [""] + rec_section + lat_section + other_section + notes), summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parity report for one face-bench run")
    parser.add_argument("run_dir")
    parser.add_argument("--ours", default="legacy_fixed")
    parser.add_argument("--ref", default="arcsoft")
    parser.add_argument("--far", type=float, action="append", help="target FAR, first one is primary (default 1e-5, 1e-4)")
    parser.add_argument("--window", type=int, default=3)
    parser.add_argument("--required", type=int, default=2)
    parser.add_argument("--min-attacks", type=int, default=100,
                        help="minimum attack clips per type before a liveness verdict (default 100)")
    parser.add_argument("--out", help="markdown output (default RUN_DIR/report.md)")
    args = parser.parse_args(argv)
    run = load_run(args.run_dir)
    fars = args.far or [1e-5, 1e-4]
    text, summary = build(run, args.ours, args.ref, fars, liveness.Rule(args.window, args.required), args.min_attacks)
    out = Path(args.out) if args.out else Path(args.run_dir) / "report.md"
    out.write_text(text, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"report: {out}")
    for dimension, verdict in summary["verdicts"].items():
        print(f"{dimension}: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
