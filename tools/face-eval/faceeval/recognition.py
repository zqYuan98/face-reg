"""Verification (1:1) and identification (1:N) metrics from scores.csv.

Every backend's scores stay on its own scale. Comparisons are made at equal false accept rates:
each backend's threshold is set from its own impostor scores.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .data import Run
from .stats import Rate

PASS, WORSE, INSUFFICIENT = "PASS", "WORSE", "INSUFFICIENT"
#: An operating point needs at least this many expected false accepts to be measurable.
MIN_EXPECTED_FALSE_ACCEPTS = 3


@dataclass
class Split:
    genuine: list[float]
    impostor: list[float]
    # (clip_id, frame_index) -> (true subject, [(enrolled_id, score), ...])
    probes: dict
    enrolled: set


def split(run: Run, backend: str, labels: tuple[str, ...] = ("live",)) -> Split:
    genuine: list[float] = []
    impostor: list[float] = []
    probes: dict = {}
    enrolled: set = set()
    for b, clip_id, frame_index, subject, label, enrolled_id, score in run.scores:
        if b != backend or label not in labels:
            continue
        enrolled.add(enrolled_id)
        (genuine if enrolled_id == subject else impostor).append(score)
        probes.setdefault((clip_id, frame_index), (subject, []))[1].append((enrolled_id, score))
    return Split(genuine, impostor, probes, enrolled)


@dataclass
class FarPoint:
    far_target: float
    threshold: float
    far_achieved: float
    impostor_count: int
    sufficient: bool
    tar: Rate


def threshold_at_far(impostor: list[float], far: float) -> tuple[float, float, bool]:
    """Smallest threshold t with (#impostor >= t) / N <= far. Returns (t, achieved FAR, sufficient)."""
    n = len(impostor)
    if n == 0:
        return math.nan, math.nan, False
    ordered = sorted(impostor, reverse=True)
    allowed = math.floor(far * n)
    threshold = math.nextafter(ordered[allowed], math.inf) if allowed < n else -math.inf
    achieved = sum(score >= threshold for score in impostor) / n
    return threshold, achieved, n * far >= MIN_EXPECTED_FALSE_ACCEPTS


def at_far(data: Split, far: float) -> FarPoint:
    threshold, achieved, sufficient = threshold_at_far(data.impostor, far)
    accepted = 0 if math.isnan(threshold) else sum(score >= threshold for score in data.genuine)
    return FarPoint(far, threshold, achieved, len(data.impostor), sufficient, Rate(accepted, len(data.genuine)))


def identification(data: Split, threshold: float) -> tuple[Rate, Rate]:
    """(rank-1 identification rate for mated probes, false positive identification rate for non-mated)."""
    mated = [0, 0]
    non_mated = [0, 0]
    for subject, candidates in data.probes.values():
        best_id, best_score = max(candidates, key=lambda item: item[1])
        hit = not math.isnan(threshold) and best_score >= threshold
        if subject in data.enrolled:
            mated[0] += hit and best_id == subject
            mated[1] += 1
        else:
            non_mated[0] += hit
            non_mated[1] += 1
    return Rate(*mated), Rate(*non_mated)


def compare(ours: FarPoint, ref: FarPoint) -> str:
    if not (ours.sufficient and ref.sufficient) or not ours.tar.n or not ref.tar.n:
        return INSUFFICIENT
    return PASS if ours.tar.value >= ref.tar.value else WORSE
