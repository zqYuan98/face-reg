"""Privacy-safe score and sampling aggregates for video verification."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = ["ScoreAggregate", "aggregate_scores", "longest_no_face_gap"]


@dataclass(frozen=True, slots=True)
class ScoreAggregate:
    valid_count: int
    median_score: float | None
    percentile_score: float | None
    minimum_score: float | None
    pass_ratio: float | None


def _finite_number(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise TypeError(f"{label} must be a finite number")
    return float(value)


def aggregate_scores(
    scores: tuple[float, ...],
    *,
    frame_threshold: float | None,
    percentile: float,
) -> ScoreAggregate:
    """Return median, low percentile, minimum and signed-threshold pass ratio."""

    if not isinstance(scores, tuple):
        raise TypeError("scores must be a tuple")
    threshold = (
        None
        if frame_threshold is None
        else _finite_number(frame_threshold, "frame_threshold")
    )
    percentile = _finite_number(percentile, "percentile")
    if threshold is not None and not 0.0 <= threshold <= 1.0:
        raise ValueError("frame_threshold must be in [0, 1]")
    if not 0.0 <= percentile <= 1.0:
        raise ValueError("percentile must be in [0, 1]")
    validated: list[float] = []
    for raw_score in scores:
        score = _finite_number(raw_score, "score")
        if not -1.0 <= score <= 1.0:
            raise ValueError("scores must be in [-1, 1]")
        validated.append(score)
    if not validated:
        return ScoreAggregate(
            valid_count=0,
            median_score=None,
            percentile_score=None,
            minimum_score=None,
            pass_ratio=None,
        )
    values = np.asarray(validated, dtype=np.float64)
    return ScoreAggregate(
        valid_count=len(validated),
        median_score=float(np.median(values)),
        percentile_score=float(np.quantile(values, percentile, method="linear")),
        minimum_score=float(np.min(values)),
        pass_ratio=(
            None
            if threshold is None
            else float(np.count_nonzero(values >= threshold) / values.size)
        ),
    )


def longest_no_face_gap(
    *,
    timestamps: tuple[float, ...],
    no_face: tuple[bool, ...],
    duration_seconds: float,
) -> float:
    """Estimate the longest sampled absence interval, including video edges."""

    if not isinstance(timestamps, tuple) or not isinstance(no_face, tuple):
        raise TypeError("timestamps and no_face must be tuples")
    if len(timestamps) != len(no_face):
        raise ValueError("timestamps and no_face must have equal lengths")
    duration = _finite_number(duration_seconds, "duration_seconds")
    if duration < 0.0:
        raise ValueError("duration_seconds must be non-negative")
    values = tuple(_finite_number(item, "timestamp") for item in timestamps)
    if any(item < 0.0 or item > duration for item in values):
        raise ValueError("timestamps must fall within the video duration")
    if any(current <= previous for previous, current in zip(values, values[1:])):
        raise ValueError("timestamps must be strictly increasing")
    if not all(type(item) is bool for item in no_face):
        raise TypeError("no_face flags must be booleans")

    longest = 0.0
    gap_start: float | None = None
    for timestamp, absent in zip(values, no_face):
        if absent and gap_start is None:
            gap_start = timestamp
        elif not absent and gap_start is not None:
            longest = max(longest, timestamp - gap_start)
            gap_start = None
    if gap_start is not None:
        longest = max(longest, duration - gap_start)
    return longest
