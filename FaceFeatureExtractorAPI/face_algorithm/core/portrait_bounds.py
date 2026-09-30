"""Fixed source-pixel overflow budget for portrait generation only."""

from __future__ import annotations

import math
from numbers import Integral

from face_algorithm.schemas import FaceBox

_MAX_SIDE_OVERFLOW_RATIO = 0.05
_MIN_VISIBLE_AREA_RATIO = 0.90


def portrait_box_within_overflow_budget(
    raw_box: FaceBox, shape: tuple[int, ...]
) -> bool:
    """Accept finite boxes retaining enough real pixels, without modifying them.

    Each side may extend by at most 5% of the original corresponding dimension;
    the visible intersection must retain at least 90% of the original area.
    Both thresholds are inclusive. This does not validate landmarks or change
    the shared selector; callers must first establish a valid selected face.
    """

    if (
        not isinstance(shape, tuple)
        or len(shape) not in (2, 3)
        or any(isinstance(value, bool) or not isinstance(value, Integral) or value <= 0
               for value in shape)
    ):
        return False
    try:
        height, width = map(float, shape[:2])
        x1, y1, x2, y2 = (
            float(raw_box.x1), float(raw_box.y1),
            float(raw_box.x2), float(raw_box.y2),
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(value) for value in (height, width, x1, y1, x2, y2)):
        return False
    raw_width, raw_height = x2 - x1, y2 - y1
    if not all(math.isfinite(value) and value > 0 for value in (raw_width, raw_height)):
        return False
    visible_width = min(x2, width) - max(x1, 0.0)
    visible_height = min(y2, height) - max(y1, 0.0)
    if visible_width <= 0 or visible_height <= 0:
        return False
    overflow_ratios = (
        max(0.0, -x1) / raw_width,
        max(0.0, -y1) / raw_height,
        max(0.0, x2 - width) / raw_width,
        max(0.0, y2 - height) / raw_height,
    )
    raw_area = raw_width * raw_height
    if not math.isfinite(raw_area) or raw_area <= 0:
        return False
    visible_area_ratio = (visible_width * visible_height) / raw_area
    return (
        all(ratio <= _MAX_SIDE_OVERFLOW_RATIO for ratio in overflow_ratios)
        and visible_area_ratio >= _MIN_VISIBLE_AREA_RATIO
    )
