"""Presence-only streaming sample contracts and worst-case budget planning.

The plan reserves the real terminal frame before allocating any windows. It
describes candidate transport, not a guarantee of per-frame face comparison.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

__all__ = ["PresenceDecodeRequest", "PresenceSampleFrame", "PresenceSamplingPlan",
           "build_presence_sampling_plan"]


def _positive_finite(value: float, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class PresenceDecodeRequest:
    sample_interval_seconds: float
    max_supplemental_frames: int = 2

    def __post_init__(self) -> None:
        _positive_finite(self.sample_interval_seconds, "sample_interval_seconds")
        if type(self.max_supplemental_frames) is not int or not 0 <= self.max_supplemental_frames <= 2:
            raise ValueError("max_supplemental_frames must be an integer from 0 to 2")


@dataclass(frozen=True, slots=True)
class PresenceSampleFrame:
    index: int
    timestamp_seconds: float
    image: np.ndarray = field(repr=False, compare=False)
    window_index: int
    kind: Literal["base", "supplemental"]


@dataclass(frozen=True, slots=True)
class PresenceSamplingPlan:
    sampling_interval_seconds: float
    max_supplemental_frames: int
    window_count: int
    frame_budget: int
    duration_seconds: float


def build_presence_sampling_plan(request: PresenceDecodeRequest, duration_seconds: float,
                                max_frames: int) -> PresenceSamplingPlan:
    """Allocate a constant-space plan covering the estimated duration.

    With a one-frame budget only the first frame can be retained; the decoder
    must mark any actual multi-frame stream incomplete. Resource budgets are
    server-configured; 1200 is the default, not a silently imposed second cap.
    """
    if not isinstance(request, PresenceDecodeRequest):
        raise TypeError("request must be a PresenceDecodeRequest")
    _positive_finite(duration_seconds, "duration_seconds")
    if type(max_frames) is not int or max_frames <= 0:
        raise ValueError("max_frames must be a positive integer")
    supplemental = min(request.max_supplemental_frames, max(0, max_frames - 2))
    capacity = max(1, (max_frames - 1) // (1 + supplemental))
    interval = max(float(request.sample_interval_seconds), duration_seconds / capacity)
    # Upward rounding prevents a floating boundary from allocating one extra
    # window or ending microscopically before the promised estimated horizon.
    if interval * capacity < duration_seconds:
        interval = math.nextafter(interval, math.inf)
    windows = min(capacity, max(1, math.ceil(duration_seconds / interval)))
    return PresenceSamplingPlan(interval, supplemental, windows, max_frames, float(duration_seconds))
