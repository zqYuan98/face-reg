"""Pure deterministic frame-index sampling."""

from __future__ import annotations

import math

__all__ = ["full_sample_indices", "quick_sample_indices"]


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def quick_sample_indices(
    *,
    total_frames: int,
    sample_count: int,
    include_first_last: bool,
) -> tuple[int, ...]:
    """Return reproducible, increasing quick-check frame indices."""

    total_frames = _positive_integer(total_frames, "total_frames")
    sample_count = _positive_integer(sample_count, "sample_count")
    if type(include_first_last) is not bool:
        raise TypeError("include_first_last must be a boolean")
    if total_frames <= sample_count:
        return tuple(range(total_frames))
    if sample_count == 1:
        return (0 if include_first_last else total_frames // 2,)

    if include_first_last:
        denominator = 2 * (sample_count - 1)
        indices = tuple(
            (2 * index * (total_frames - 1) + sample_count - 1) // denominator
            for index in range(sample_count)
        )
    else:
        indices = tuple(
            ((2 * index + 1) * total_frames) // (2 * sample_count)
            for index in range(sample_count)
        )
    return tuple(dict.fromkeys(indices))


def full_sample_indices(
    *,
    total_frames: int,
    fps: float,
    interval_seconds: float,
    max_frames: int,
) -> tuple[int, ...]:
    """Return increasing time-based indices without exceeding ``max_frames``."""

    total_frames = _positive_integer(total_frames, "total_frames")
    max_frames = _positive_integer(max_frames, "max_frames")
    if (
        isinstance(fps, bool)
        or not isinstance(fps, (int, float))
        or not math.isfinite(float(fps))
        or float(fps) <= 0.0
    ):
        raise ValueError("fps must be a finite positive number")
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, (int, float))
        or not math.isfinite(float(interval_seconds))
        or float(interval_seconds) <= 0.0
    ):
        raise ValueError("interval_seconds must be a finite positive number")

    result: list[int] = []
    frame_index = 0
    step = float(fps) * float(interval_seconds)
    while frame_index < total_frames and len(result) < max_frames:
        result.append(frame_index)
        next_index = int(round(len(result) * step))
        frame_index = max(frame_index + 1, next_index)
    return tuple(result)
