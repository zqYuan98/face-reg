"""Per-stage timing statistics from frames.csv, excluding per-clip warm-up frames."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .data import Run
from .stats import percentile

STAGES = ("preprocess_ms", "detect_ms", "liveness_ms", "feature_ms")
#: Stages compared for parity. The full pipeline is the fairest single number because backends
#: split work differently (the legacy recognizer detects and aligns inside "feature").
COMPARED = ("full_pipeline_ms", "detect_ms", "liveness_ms", "feature_ms")
PASS, WORSE, NOT_COMPARABLE = "PASS", "WORSE", "N/A"


@dataclass
class StageStats:
    count: int
    mean: float
    p50: float
    p95: float
    max: float


def _stats(values: list[float]) -> StageStats:
    if not values:
        return StageStats(0, math.nan, math.nan, math.nan, math.nan)
    return StageStats(len(values), sum(values) / len(values), percentile(values, 50), percentile(values, 95), max(values))


def stage_stats(run: Run, backend: str) -> dict[str, StageStats]:
    rows = [r for r in run.frames if r.backend == backend and not r.warmup]
    result = {stage: _stats([getattr(r, stage) for r in rows if not math.isnan(getattr(r, stage))]) for stage in STAGES}
    full = []
    for r in rows:
        # A complete per-frame pipeline: a face was found, liveness ran and a feature was attempted.
        if r.feature_attempted and r.face_count > 0 and not math.isnan(r.liveness_ms) and not math.isnan(r.feature_ms):
            full.append(sum(v for v in (getattr(r, s) for s in STAGES) if not math.isnan(v)))
    result["full_pipeline_ms"] = _stats(full)
    return result


def compare(ours: dict[str, StageStats], ref: dict[str, StageStats]) -> dict[str, str]:
    verdicts = {}
    for stage in COMPARED:
        mine, theirs = ours.get(stage), ref.get(stage)
        if not mine or not theirs or not mine.count or not theirs.count:
            verdicts[stage] = NOT_COMPARABLE
        else:
            verdicts[stage] = PASS if mine.p50 <= theirs.p50 and mine.p95 <= theirs.p95 else WORSE
    return verdicts
