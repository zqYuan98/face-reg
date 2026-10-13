"""Writes a run directory in exactly the format android-sdk/face-bench produces."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

FRAME_HEADER = ["backend", "clip_id", "subject_id", "label", "attack_type", "frame_index", "timestamp_ns", "face_count",
                "box_left", "box_top", "box_right", "box_bottom", "preprocess_ms", "detect_ms", "liveness_ms",
                "feature_ms", "live_score", "live_status", "feature_attempted", "feature_ok", "warmup", "note"]
SWEEP_HEADER = ["backend", "clip_id", "frame_index", "threshold", "status"]
SCORE_HEADER = ["backend", "clip_id", "frame_index", "subject_id", "label", "enrolled_id", "score"]
THRESHOLDS = [0.1, 0.3, 0.5, 0.7, 0.9]


def write_run(root: Path, *, subjects=20, attacks_per_type=5, frames=6, seed=7,
              ours_live_margin=0.25, ref_attack_leak=0.0, ours_ms=20.0, ref_ms=30.0) -> Path:
    """Two backends: 'ours' (continuous liveness scores) and 'ref' (ArcSoft-style status sweep).

    Real faces score high for both; attacks score low. ``ref_attack_leak`` makes some ref attack
    frames look live at every threshold, so ref's APCER is worse when it is positive.
    """
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    clips = [(f"s{i:03d}_live", f"s{i:03d}", "live", "") for i in range(subjects)]
    for attack in ("print", "screen"):
        clips += [(f"s{i:03d}_{attack}", f"s{i:03d}", "attack", attack) for i in range(attacks_per_type)]
    enrolled = [f"s{i:03d}" for i in range(subjects)]
    with (root / "frames.csv").open("w", newline="", encoding="utf-8") as f_frames, \
            (root / "liveness_sweep.csv").open("w", newline="", encoding="utf-8") as f_sweep, \
            (root / "scores.csv").open("w", newline="", encoding="utf-8") as f_scores:
        frames_w, sweep_w, scores_w = csv.writer(f_frames), csv.writer(f_sweep), csv.writer(f_scores)
        frames_w.writerow(FRAME_HEADER)
        sweep_w.writerow(SWEEP_HEADER)
        scores_w.writerow(SCORE_HEADER)
        for backend in ("ours", "ref"):
            for clip_id, subject, label, attack in clips:
                for index in range(frames):
                    live_prob = (0.5 + ours_live_margin if label == "live" else 0.5 - ours_live_margin)
                    score = min(0.999, max(0.001, rng.gauss(live_prob, 0.05)))
                    feature = index % 2 == 0
                    ms = ours_ms if backend == "ours" else ref_ms
                    status = ""
                    live_score = ""
                    if backend == "ours":
                        live_score = f"{score:.6f}"
                        status = 1 if score > 0.7 else 0
                    else:
                        leak = label == "attack" and rng.random() < ref_attack_leak
                        for t in THRESHOLDS:
                            alive = leak or score > t
                            sweep_w.writerow([backend, clip_id, index, f"{t:.6f}", 1 if alive else 0])
                        status = 1 if (leak or score > 0.5) else 0
                    frames_w.writerow([backend, clip_id, subject, label, attack, index, index * 33_000_000, 1,
                                       10, 10, 100, 100, "", f"{ms * 0.3:.4f}", f"{ms * 0.3:.4f}",
                                       f"{ms * 0.4:.4f}" if feature else "", live_score, status,
                                       1 if feature else 0, 1 if feature else 0, 1 if index < 1 else 0, ""])
                    if feature:
                        for other in enrolled:
                            mated = other == subject
                            value = rng.gauss(0.8 if mated else 0.2, 0.05)
                            scores_w.writerow([backend, clip_id, index, subject, label, other, f"{value:.6f}"])
    (root / "run.json").write_text(json.dumps({
        "runId": root.name, "device": {"manufacturer": "Test", "model": "Synthetic", "androidRelease": "13"},
        "backends": {"ours": "synthetic scores", "ref": "synthetic statuses"}, "failures": {}, "cancelled": False,
    }), encoding="utf-8")
    return root
