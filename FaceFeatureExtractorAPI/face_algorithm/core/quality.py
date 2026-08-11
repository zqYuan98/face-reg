"""Deterministic, finite face quality metrics."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from face_algorithm.schemas import Pose, QualityMetric, QualityResult, ReasonCode

__all__ = ["evaluate_quality", "clarity_score"]


def _safe_edge_blur_ratio(data: np.ndarray, axis: int) -> float:
    kernel = np.ones((9,), dtype=np.float32) / 9.0
    kernel_2d = kernel.reshape((-1, 1) if axis == 0 else (1, -1))
    blurred = cv2.filter2D(data, -1, kernel_2d, borderType=cv2.BORDER_REPLICATE)
    difference = np.abs(np.diff(data, axis=axis))
    blurred_difference = np.abs(np.diff(blurred, axis=axis))
    total = float(np.sum(difference, dtype=np.float64))
    if not np.isfinite(total) or total <= np.finfo(np.float32).eps:
        return 1.0
    visible = float(
        np.sum(np.maximum(0.0, difference - blurred_difference), dtype=np.float64)
    )
    ratio = (total - visible) / total
    return float(np.clip(ratio if np.isfinite(ratio) else 1.0, 0.0, 1.0))


def clarity_score(image: np.ndarray) -> float:
    """Return legacy-compatible clarity in ``[0, 1]`` without NaNs."""

    if not isinstance(image, np.ndarray) or image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    if gray.shape[0] < 4 or gray.shape[1] < 4:
        return 0.0
    scores: list[float] = []
    for row in np.array_split(gray, 2, axis=0):
        for cell in np.array_split(row, 2, axis=1):
            if cell.shape[0] < 2 or cell.shape[1] < 2:
                scores.append(0.0)
                continue
            blur = max(
                _safe_edge_blur_ratio(cell, axis=0),
                _safe_edge_blur_ratio(cell, axis=1),
            )
            scores.append(float(np.clip(1.0 - blur, 0.0, 1.0)))
    value = min(scores, default=0.0)
    return value if np.isfinite(value) else 0.0


def _grid_max_brightness(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    values = [
        float(cell.mean())
        for row in np.array_split(gray, 3, axis=0)
        for cell in np.array_split(row, 3, axis=1)
        if cell.size
    ]
    value = max(values, default=0.0)
    return value if np.isfinite(value) else 0.0


def evaluate_quality(
    face_image: np.ndarray,
    *,
    original_face_size: tuple[float, float],
    pose: Pose,
    profile: Any,
) -> QualityResult:
    """Evaluate brightness, raw resolution, clarity, and all three pose axes."""

    width, height = original_face_size
    brightness = _grid_max_brightness(face_image)
    brightness_passed = profile.brightness.v1 <= brightness <= profile.brightness.v2
    if brightness < profile.brightness.v1:
        brightness_reason = ReasonCode.TOO_DARK
    elif brightness > profile.brightness.v2:
        brightness_reason = ReasonCode.TOO_BRIGHT
    else:
        brightness_reason = None

    resolution_value = min(
        width / float(profile.resolution.width),
        height / float(profile.resolution.height),
    )
    resolution_value = float(resolution_value if np.isfinite(resolution_value) else 0.0)
    resolution_passed = resolution_value >= 1.0

    clarity = clarity_score(face_image)
    clarity_passed = clarity >= profile.clarity.low_threshold
    yaw_passed = abs(pose.yaw) <= profile.pose.yaw_threshold
    pitch_passed = abs(pose.pitch) <= profile.pose.pitch_threshold
    roll_passed = abs(pose.roll) <= profile.pose.roll_threshold

    metrics = (
        QualityMetric(name="brightness", value=brightness, passed=brightness_passed),
        QualityMetric(name="resolution", value=resolution_value, passed=resolution_passed),
        QualityMetric(name="clarity", value=clarity, passed=clarity_passed),
        QualityMetric(name="yaw", value=pose.yaw, passed=yaw_passed),
        QualityMetric(name="pitch", value=pose.pitch, passed=pitch_passed),
        QualityMetric(name="roll", value=pose.roll, passed=roll_passed),
    )
    reasons: list[ReasonCode] = []
    if brightness_reason is not None:
        reasons.append(brightness_reason)
    if not resolution_passed:
        reasons.append(ReasonCode.FACE_TOO_SMALL)
    if not clarity_passed:
        reasons.append(ReasonCode.TOO_BLURRY)
    if not (yaw_passed and pitch_passed and roll_passed):
        reasons.append(ReasonCode.BAD_POSE)
    unique_reasons = tuple(dict.fromkeys(reasons))
    return QualityResult(
        passed=not unique_reasons,
        metrics=metrics,
        reason_codes=unique_reasons,
    )
