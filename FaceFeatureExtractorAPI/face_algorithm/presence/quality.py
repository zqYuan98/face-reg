"""Presence-only technical alignment checks and non-blocking quality warnings."""

from __future__ import annotations

from typing import Any

import numpy as np

from face_algorithm.core.quality import evaluate_quality
from face_algorithm.schemas import FaceBox, Pose, ReasonCode
from models.facealign import FaceAlign, transformation_maker

__all__ = ["alignment_issue", "evaluate_quality_warnings"]


def alignment_issue(landmarks: np.ndarray, image_shape: tuple[int, ...]) -> ReasonCode | None:
    """Require a nondegenerate transform with real source samples, not good pose.

    The transform and bilinear support match the existing 256px recognizer
    alignment protocol. Checking source coordinates, rather than output colour,
    distinguishes entirely padded crops from genuine dark source images.
    """
    if landmarks.shape != (5, 2):
        return ReasonCode.CANDIDATE_LANDMARK_INVALID
    if landmarks.dtype.kind not in "fiu":
        raise TypeError("landmark coordinates must be real numeric values")
    if not np.all(np.isfinite(landmarks)):
        return ReasonCode.CANDIDATE_LANDMARK_INVALID
    points = landmarks.astype(np.float64, copy=True)
    # Rank tests use machine precision, not eye-distance or pose heuristics.
    centered = points - points.mean(axis=0)
    magnitude = float(np.max(np.abs(centered)))
    if not np.isfinite(magnitude) or magnitude == 0.0:
        return ReasonCode.CANDIDATE_ALIGNMENT_INVALID
    if np.linalg.matrix_rank(centered / magnitude) < 2:
        return ReasonCode.CANDIDATE_ALIGNMENT_INVALID
    protocol = FaceAlign()
    template = np.asarray(protocol.mean_face, dtype=np.float32).reshape(5, 2).astype(np.float64)
    template -= template.mean(axis=0)
    template_norm = float(np.linalg.norm(template))
    covariance = float(np.sum(centered * template))
    cross_covariance = float(np.sum(centered[:, 1] * template[:, 0] - centered[:, 0] * template[:, 1]))
    fitted_extent = float(np.hypot(covariance, cross_covariance) / template_norm)
    # A full-rank point cloud can still have a zero similarity fit. Reject fits
    # indistinguishable from float32 input roundoff, before the legacy formula
    # amplifies cancellation into a spurious tiny-but-nonzero transform.
    coordinate_roundoff = np.finfo(np.float32).eps * float(np.linalg.norm(points))
    if not np.isfinite(fitted_extent) or fitted_extent <= coordinate_roundoff:
        return ReasonCode.CANDIDATE_ALIGNMENT_INVALID
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        success, transform = transformation_maker(
            256, 256, points, protocol.mean_face, 256, 256,
        )
    if not success or transform is None or not np.all(np.isfinite(transform)):
        return ReasonCode.CANDIDATE_ALIGNMENT_INVALID
    scale = float(np.hypot(transform[0, 0], transform[1, 0]))
    if not np.isfinite(scale) or scale == 0.0:
        return ReasonCode.CANDIDATE_ALIGNMENT_INVALID
    height, width = image_shape[:2]
    rows, columns = np.indices((256, 256), dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        source_x = transform[0, 0] * columns + transform[0, 1] * rows + transform[0, 2]
        source_y = transform[1, 0] * columns + transform[1, 1] * rows + transform[1, 2]
    if not np.all(np.isfinite(source_x)) or not np.all(np.isfinite(source_y)):
        return ReasonCode.CANDIDATE_ALIGNMENT_INVALID
    # The production sampler requires the complete 2x2 interpolation footprint.
    visible = (source_x >= 0) & (source_x < width - 1) & (source_y >= 0) & (source_y < height - 1)
    if not np.any(visible):
        return ReasonCode.CANDIDATE_ALIGNMENT_INVALID
    return None


def evaluate_quality_warnings(
    image: np.ndarray, *, box: FaceBox, landmarks: np.ndarray, pose: Pose, profile: Any,
) -> tuple[ReasonCode, ...]:
    """Return stable soft warnings for a clipped, nonempty visible face ROI."""
    x1, y1 = int(np.floor(box.x1)), int(np.floor(box.y1))
    x2, y2 = int(np.ceil(box.x2)), int(np.ceil(box.y2))
    visible = image[y1:y2, x1:x2]
    quality = evaluate_quality(
        visible, original_face_size=(box.width, box.height), pose=pose, profile=profile,
    )
    warnings = list(quality.reason_codes)
    height, width = image.shape[:2]
    margin = max(1.0, min(height, width) * profile.border_margin_ratio)
    if (np.any(landmarks[:, 0] <= margin) or np.any(landmarks[:, 1] <= margin)
            or np.any(landmarks[:, 0] >= width - margin) or np.any(landmarks[:, 1] >= height - margin)):
        warnings.append(ReasonCode.FACE_CROPPED)
    return tuple(dict.fromkeys(warnings))
