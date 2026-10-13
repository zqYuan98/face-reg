"""Loads one face-bench run directory (frames.csv, liveness_sweep.csv, scores.csv, run.json)."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path


def _float(text: str) -> float:
    return float(text) if text not in ("", None) else math.nan


@dataclass
class FrameRow:
    backend: str
    clip_id: str
    subject_id: str
    label: str
    attack_type: str
    frame_index: int
    timestamp_ns: int
    face_count: int
    preprocess_ms: float
    detect_ms: float
    liveness_ms: float
    feature_ms: float
    live_score: float
    live_status: int
    feature_attempted: bool
    feature_ok: bool
    warmup: bool


@dataclass
class Run:
    path: Path
    meta: dict
    frames: list[FrameRow] = field(default_factory=list)
    # (backend, clip_id) -> frame_index -> threshold -> status
    sweep: dict = field(default_factory=dict)
    # rows of (backend, clip_id, frame_index, subject_id, label, enrolled_id, score)
    scores: list[tuple] = field(default_factory=list)

    def backends(self) -> list[str]:
        seen: list[str] = []
        for row in self.frames:
            if row.backend not in seen:
                seen.append(row.backend)
        return seen


def _rows(path: Path):
    if not path.is_file():
        return
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def load_run(path: str | Path) -> Run:
    root = Path(path)
    meta_file = root / "run.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.is_file() else {}
    run = Run(root, meta)
    for r in _rows(root / "frames.csv"):
        run.frames.append(FrameRow(
            backend=r["backend"], clip_id=r["clip_id"], subject_id=r["subject_id"], label=r["label"],
            attack_type=r["attack_type"] or "", frame_index=int(r["frame_index"]),
            timestamp_ns=int(r["timestamp_ns"]), face_count=int(r["face_count"] or 0),
            preprocess_ms=_float(r["preprocess_ms"]), detect_ms=_float(r["detect_ms"]),
            liveness_ms=_float(r["liveness_ms"]), feature_ms=_float(r["feature_ms"]),
            live_score=_float(r["live_score"]), live_status=int(r["live_status"]),
            feature_attempted=r["feature_attempted"] == "1", feature_ok=r["feature_ok"] == "1",
            warmup=r["warmup"] == "1"))
    for r in _rows(root / "liveness_sweep.csv"):
        key = (r["backend"], r["clip_id"])
        threshold = round(float(r["threshold"]), 4)
        run.sweep.setdefault(key, {}).setdefault(int(r["frame_index"]), {})[threshold] = int(r["status"])
    for r in _rows(root / "scores.csv"):
        run.scores.append((r["backend"], r["clip_id"], int(r["frame_index"]), r["subject_id"], r["label"],
                           r["enrolled_id"], float(r["score"])))
    return run
