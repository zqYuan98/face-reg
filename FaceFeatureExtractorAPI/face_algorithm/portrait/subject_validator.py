"""Measure local visible head/shoulders and apply the selected portrait policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from face_algorithm.schemas import FaceBox, ReasonCode

__all__ = [
    "PortraitSubjectValidation",
    "PortraitSubjectValidationError",
    "validate_portrait_subject",
]


@dataclass(frozen=True, slots=True)
class PortraitSubjectValidation:
    """Non-biometric aggregate geometry used by portrait composition."""

    robust_core_top: int
    top_edge_core_coverage: float
    shoulder_depth_ratio: float
    shoulder_band_coverage: float
    shoulder_left_coverage: float
    shoulder_right_coverage: float
    shoulder_valid_row_ratio: float
    reason_codes: tuple[ReasonCode, ...] = ()


class PortraitSubjectValidationError(ValueError):
    """A recapture condition found in the required head/shoulder region."""

    def __init__(self, reason_code: ReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.value)


def _setting(template: Any, name: str, default: float | int) -> float:
    value = getattr(template, name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _coverage(mask: np.ndarray) -> float:
    if mask.size == 0:
        return 0.0
    return float(np.count_nonzero(mask)) / float(mask.size)


def validate_portrait_subject(
    core: np.ndarray,
    face: FaceBox,
    template: Any,
) -> PortraitSubjectValidation:
    """Measure local subject regions, requiring completeness only in strict mode.

    ``core`` is the selected hard person component. Sparse matte tails and
    pixels outside the local head/shoulder ROIs deliberately do not affect the
    decision.
    """

    if not (
        isinstance(core, np.ndarray)
        and core.ndim == 2
        and core.size > 0
        and isinstance(face, FaceBox)
    ):
        raise TypeError("core must be a non-empty 2D array and face a FaceBox")
    hard = np.asarray(core > 0, dtype=np.uint8)
    height, width = hard.shape
    archive = getattr(template, "mode", "head_shoulders") == "face_archive"
    reasons: tuple[ReasonCode, ...] = ()

    head_expansion = _setting(template, "head_roi_expansion_ratio", 0.25)
    head_x1 = max(0, min(width, int(np.floor(face.x1 - head_expansion * face.width))))
    head_x2 = max(0, min(width, int(np.ceil(face.x2 + head_expansion * face.width))))
    if head_x2 <= head_x1:
        raise PortraitSubjectValidationError(ReasonCode.MATTING_LOW_CONFIDENCE)

    robust_width_ratio = _setting(
        template,
        "robust_top_min_row_width_ratio",
        0.05,
    )
    robust_min_pixels = max(1, int(np.ceil(robust_width_ratio * face.width)))
    head_row_counts = np.count_nonzero(hard[:, head_x1:head_x2], axis=1)

    top_edge_min_pixels = int(
        np.ceil(_setting(template, "top_edge_min_pixels", 2))
    )
    top_edge_ratio_pixels = int(
        np.ceil(_setting(template, "top_edge_band_ratio", 0.005) * height)
    )
    top_edge_band_height = min(
        height,
        max(1, top_edge_min_pixels, top_edge_ratio_pixels),
    )
    top_edge_core_coverage = _coverage(
        hard[:top_edge_band_height, head_x1:head_x2]
    )
    required_headroom = (
        _setting(template, "required_headroom_ratio", 0.12) * face.height
    )
    min_top_coverage = _setting(
        template,
        "min_top_edge_core_coverage",
        0.15,
    )
    placement_min_pixels = max(
        robust_min_pixels,
        int(np.ceil(min_top_coverage * float(head_x2 - head_x1))),
    )
    placement_rows = np.flatnonzero(head_row_counts >= placement_min_pixels)
    if placement_rows.size == 0:
        raise PortraitSubjectValidationError(ReasonCode.MATTING_LOW_CONFIDENCE)
    robust_core_top = int(placement_rows[0])
    available_headroom = face.y1 - float(robust_core_top)
    if (
        robust_core_top < top_edge_band_height
        and top_edge_core_coverage >= min_top_coverage
    ):
        # Archive warns about reliable source-edge truncation regardless of
        # forehead/headroom size; strict mode retains its original crop gate.
        if archive:
            reasons = (ReasonCode.PORTRAIT_SOURCE_LIMITED,)
        elif available_headroom < required_headroom:
            raise PortraitSubjectValidationError(ReasonCode.SUBJECT_CROPPED)

    shoulder_depth_ratio = _setting(template, "min_shoulder_depth_ratio", 0.60)
    shoulder_y1 = max(0, min(height, int(np.floor(face.y2))))
    required_shoulder_y2 = face.y2 + shoulder_depth_ratio * face.height
    # Strict mode must not accept a shortened shoulder band. Archive mode
    # measures only the visible region and never invents missing shoulders.
    if not archive and required_shoulder_y2 > float(height):
        raise PortraitSubjectValidationError(ReasonCode.SHOULDERS_NOT_VISIBLE)
    shoulder_y2 = max(0, min(height, int(np.ceil(required_shoulder_y2))))
    shoulder_width = _setting(template, "shoulder_roi_width_ratio", 1.30) * face.width
    face_center_x = (face.x1 + face.x2) / 2.0
    shoulder_x1 = max(
        0,
        min(width, int(np.floor(face_center_x - shoulder_width / 2.0))),
    )
    shoulder_x2 = max(
        0,
        min(width, int(np.ceil(face_center_x + shoulder_width / 2.0))),
    )
    if not archive and (shoulder_y2 <= shoulder_y1 or shoulder_x2 - shoulder_x1 < 2):
        raise PortraitSubjectValidationError(ReasonCode.SHOULDERS_NOT_VISIBLE)

    shoulder = hard[shoulder_y1:shoulder_y2, shoulder_x1:shoulder_x2]
    split = shoulder.shape[1] // 2
    left = shoulder[:, :split]
    right = shoulder[:, split:]
    shoulder_band_coverage = _coverage(shoulder)
    shoulder_left_coverage = _coverage(left)
    shoulder_right_coverage = _coverage(right)
    row_coverage = (
        np.count_nonzero(shoulder, axis=1) / float(shoulder.shape[1])
        if shoulder.shape[1] else np.zeros(shoulder.shape[0])
    )
    shoulder_valid_row_ratio = float(
        np.count_nonzero(
            row_coverage
            >= _setting(template, "min_shoulder_row_coverage", 0.25)
        )
    ) / float(shoulder.shape[0]) if shoulder.shape[0] else 0.0

    min_band = _setting(template, "min_shoulder_band_coverage", 0.35)
    min_half = _setting(template, "min_shoulder_half_coverage", 0.20)
    min_valid_rows = _setting(template, "min_shoulder_valid_row_ratio", 0.60)
    if not archive and (
        shoulder_band_coverage < min_band
        or shoulder_left_coverage < min_half
        or shoulder_right_coverage < min_half
        or shoulder_valid_row_ratio < min_valid_rows
    ):
        raise PortraitSubjectValidationError(ReasonCode.SHOULDERS_NOT_VISIBLE)

    occupied_rows = np.flatnonzero(np.any(shoulder > 0, axis=1))
    actual_bottom = (
        float(face.y2)
        if occupied_rows.size == 0
        else float(shoulder_y1 + int(occupied_rows[-1]) + 1)
    )
    actual_shoulder_depth_ratio = max(
        0.0,
        (actual_bottom - face.y2) / face.height,
    )
    return PortraitSubjectValidation(
        robust_core_top=robust_core_top,
        top_edge_core_coverage=top_edge_core_coverage,
        shoulder_depth_ratio=actual_shoulder_depth_ratio,
        shoulder_band_coverage=shoulder_band_coverage,
        shoulder_left_coverage=shoulder_left_coverage,
        shoulder_right_coverage=shoulder_right_coverage,
        shoulder_valid_row_ratio=shoulder_valid_row_ratio,
        reason_codes=reasons,
    )
