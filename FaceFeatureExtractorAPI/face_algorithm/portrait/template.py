"""Deterministic subject selection, placement, and white composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from face_algorithm.portrait.contracts import (
    PortraitMattingQuality,
    PortraitPlacement,
)
from face_algorithm.portrait.subject_validator import (
    PortraitSubjectValidationError,
    validate_portrait_subject,
)
from face_algorithm.schemas import FaceBox, FaceLandmarks, ReasonCode

__all__: list[str] = []


class _PortraitGenerationError(ValueError):
    def __init__(self, reason_code: ReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.value)


@dataclass(frozen=True, slots=True, repr=False)
class _GeneratedPortrait:
    image: np.ndarray = field(repr=False, compare=False)
    placement: PortraitPlacement
    matting_quality: PortraitMattingQuality


def _generate_portrait_pixels(
    image: np.ndarray,
    matte: np.ndarray,
    face: FaceBox,
    landmarks: FaceLandmarks,
    template: Any,
) -> _GeneratedPortrait:
    source = np.ascontiguousarray(image.copy())
    cleaned, quality, bounds = _select_subject_matte(matte, face, template)
    placement = _compute_placement(face, landmarks, template)
    _validate_placed_geometry(bounds, face, placement, template)
    output = _composite_on_background(source, cleaned, placement, template)
    return _GeneratedPortrait(
        image=output,
        placement=placement,
        matting_quality=quality,
    )


def _face_slices(face: FaceBox, shape: tuple[int, int]) -> tuple[slice, slice]:
    height, width = shape
    x1 = max(0, min(width, int(np.floor(face.x1))))
    y1 = max(0, min(height, int(np.floor(face.y1))))
    x2 = max(0, min(width, int(np.ceil(face.x2))))
    y2 = max(0, min(height, int(np.ceil(face.y2))))
    if x2 <= x1 or y2 <= y1:
        raise _PortraitGenerationError(ReasonCode.PORTRAIT_GEOMETRY_INVALID)
    return slice(y1, y2), slice(x1, x2)


def _select_subject_matte(
    matte: np.ndarray,
    face: FaceBox,
    template: Any,
) -> tuple[np.ndarray, PortraitMattingQuality, tuple[int, int, int, int]]:
    if not (
        isinstance(matte, np.ndarray)
        and matte.dtype == np.float32
        and matte.ndim == 2
        and matte.size > 0
        and matte.flags.c_contiguous
        and np.all(np.isfinite(matte))
        and float(matte.min()) >= 0.0
        and float(matte.max()) <= 1.0
    ):
        raise TypeError("matting output violates the float32 alpha contract")

    height, width = matte.shape
    face_y, face_x = _face_slices(face, (height, width))
    hard = np.asarray(matte >= float(template.alpha_threshold), dtype=np.uint8)
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        hard,
        connectivity=8,
    )
    best_label = 0
    best_key = (0, 0)
    for label in range(1, component_count):
        overlap = int(np.count_nonzero(labels[face_y, face_x] == label))
        area = int(stats[label, cv2.CC_STAT_AREA])
        key = (overlap, area)
        if overlap > 0 and key > best_key:
            best_key = key
            best_label = label
    if best_label == 0:
        raise _PortraitGenerationError(ReasonCode.MATTING_LOW_CONFIDENCE)

    core = np.asarray(labels == best_label, dtype=np.uint8)
    subject_area_ratio = float(np.count_nonzero(core)) / float(height * width)
    if subject_area_ratio < float(template.min_subject_area_ratio):
        raise _PortraitGenerationError(ReasonCode.MATTING_LOW_CONFIDENCE)

    try:
        subject_validation = validate_portrait_subject(core, face, template)
    except PortraitSubjectValidationError as error:
        raise _PortraitGenerationError(error.reason_code) from error

    soft = np.asarray(
        matte >= float(template.edge_alpha_threshold),
        dtype=np.uint8,
    )
    soft_count, soft_labels, _soft_stats, _ = cv2.connectedComponentsWithStats(
        soft,
        connectivity=8,
    )
    overlapping_soft_labels = soft_labels[core > 0]
    label_counts = np.bincount(overlapping_soft_labels, minlength=soft_count)
    if label_counts.size <= 1:
        raise _PortraitGenerationError(ReasonCode.MATTING_LOW_CONFIDENCE)
    label_counts[0] = 0
    soft_label = int(np.argmax(label_counts))
    if label_counts[soft_label] <= 0:
        raise _PortraitGenerationError(ReasonCode.MATTING_LOW_CONFIDENCE)
    soft_component = np.asarray(soft_labels == soft_label, dtype=np.uint8)
    core_x = int(stats[best_label, cv2.CC_STAT_LEFT])
    core_y = int(stats[best_label, cv2.CC_STAT_TOP])
    core_width = int(stats[best_label, cv2.CC_STAT_WIDTH])
    core_height = int(stats[best_label, cv2.CC_STAT_HEIGHT])
    bounds = (
        core_x,
        subject_validation.robust_core_top,
        core_x + core_width,
        core_y + core_height,
    )

    radius = int(template.feather_radius_pixels)
    closed = core
    if radius > 0:
        kernel_size = radius * 2 + 1
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (kernel_size, kernel_size),
        )
        closed = cv2.morphologyEx(core, cv2.MORPH_CLOSE, kernel)
        support = np.maximum(cv2.dilate(closed, kernel), soft_component)
        repair_candidates = np.asarray(
            (closed > 0) & (core == 0),
            dtype=np.uint8,
        )
        repair_mask = np.zeros_like(repair_candidates)
        repair_count, repair_labels, repair_stats, _ = (
            cv2.connectedComponentsWithStats(repair_candidates, connectivity=8)
        )
        max_repair_area = kernel_size * kernel_size
        for repair_label in range(1, repair_count):
            repair_x = int(repair_stats[repair_label, cv2.CC_STAT_LEFT])
            repair_y = int(repair_stats[repair_label, cv2.CC_STAT_TOP])
            repair_width = int(repair_stats[repair_label, cv2.CC_STAT_WIDTH])
            repair_height = int(repair_stats[repair_label, cv2.CC_STAT_HEIGHT])
            repair_area = int(repair_stats[repair_label, cv2.CC_STAT_AREA])
            touches_edge = (
                repair_x == 0
                or repair_y == 0
                or repair_x + repair_width == width
                or repair_y + repair_height == height
            )
            if repair_area <= max_repair_area and not touches_edge:
                repair_mask[repair_labels == repair_label] = 1
        if np.any(repair_mask):
            repaired = cv2.inpaint(
                matte,
                repair_mask * np.uint8(255),
                float(max(1, radius)),
                cv2.INPAINT_TELEA,
            )
            repaired = np.clip(repaired, 0.0, 1.0).astype(np.float32)
        else:
            repaired = matte
    else:
        support = np.maximum(closed, soft_component)
        repaired = matte
    cleaned = np.where(support > 0, repaired, np.float32(0.0)).astype(
        np.float32,
        copy=False,
    )
    cleaned = np.ascontiguousarray(cleaned)

    face_values = cleaned[face_y, face_x]
    face_mean = float(np.mean(face_values, dtype=np.float64))
    face_coverage = float(
        np.count_nonzero(face_values >= float(template.alpha_threshold))
    ) / float(face_values.size)
    if (
        face_mean < float(template.min_face_alpha_mean)
        or face_coverage < float(template.min_face_alpha_coverage)
    ):
        raise _PortraitGenerationError(ReasonCode.MATTING_LOW_CONFIDENCE)

    quality = PortraitMattingQuality(
        face_mean=face_mean,
        face_coverage=face_coverage,
        subject_area_ratio=subject_area_ratio,
        shoulder_depth_ratio=subject_validation.shoulder_depth_ratio,
        shoulder_band_coverage=subject_validation.shoulder_band_coverage,
    )
    return cleaned, quality, bounds


def _compute_placement(
    face: FaceBox,
    landmarks: FaceLandmarks,
    template: Any,
) -> PortraitPlacement:
    if len(landmarks.points) != 5 or face.height <= 0:
        raise _PortraitGenerationError(ReasonCode.PORTRAIT_GEOMETRY_INVALID)
    left_eye, right_eye = landmarks.points[:2]
    eye_x = (left_eye.x + right_eye.x) / 2.0
    eye_y = (left_eye.y + right_eye.y) / 2.0
    target_face_height = float(template.output_height) * float(
        template.face_height_ratio
    )
    scale = target_face_height / face.height
    translate_x = float(template.output_width) / 2.0 - scale * eye_x
    translate_y = (
        float(template.output_height) * float(template.eye_line_y_ratio)
        - scale * eye_y
    )
    values = np.asarray((scale, translate_x, translate_y), dtype=np.float64)
    if (
        not np.all(np.isfinite(values))
        or scale <= 0.0
        or scale > float(template.max_upscale_ratio) + 1e-9
    ):
        raise _PortraitGenerationError(ReasonCode.PORTRAIT_GEOMETRY_INVALID)
    return PortraitPlacement(
        scale=scale,
        translate_x=translate_x,
        translate_y=translate_y,
        affine=(
            (scale, 0.0, translate_x),
            (0.0, scale, translate_y),
        ),
    )


def _validate_placed_geometry(
    subject_bounds: tuple[int, int, int, int],
    face: FaceBox,
    placement: PortraitPlacement,
    template: Any,
) -> None:
    scale = placement.scale
    tx = placement.translate_x
    ty = placement.translate_y
    width = float(template.output_width)
    height = float(template.output_height)
    transformed_face = (
        scale * face.x1 + tx,
        scale * face.y1 + ty,
        scale * face.x2 + tx,
        scale * face.y2 + ty,
    )
    transformed_subject = (
        scale * subject_bounds[0] + tx,
        scale * subject_bounds[1] + ty,
        scale * subject_bounds[2] + tx,
    )
    required_shoulder_y = transformed_face[3] + (
        float(template.min_shoulder_depth_ratio) * scale * face.height
    )
    top_margin = float(template.top_margin_ratio) * height
    tolerance = 1e-5
    if (
        transformed_face[0] < -tolerance
        or transformed_face[1] < -tolerance
        or transformed_face[2] > width + tolerance
        or transformed_face[3] > height + tolerance
        or transformed_subject[1] < top_margin - tolerance
        or required_shoulder_y > height + tolerance
    ):
        raise _PortraitGenerationError(ReasonCode.PORTRAIT_GEOMETRY_INVALID)


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    return np.where(
        values <= np.float32(0.04045),
        values / np.float32(12.92),
        ((values + np.float32(0.055)) / np.float32(1.055))
        ** np.float32(2.4),
    )


def _linear_to_srgb(values: np.ndarray) -> np.ndarray:
    return np.where(
        values <= np.float32(0.0031308),
        values * np.float32(12.92),
        np.float32(1.055) * np.power(values, np.float32(1.0 / 2.4))
        - np.float32(0.055),
    )


def _composite_on_background(
    image: np.ndarray,
    matte: np.ndarray,
    placement: PortraitPlacement,
    template: Any,
) -> np.ndarray:
    source_srgb = image.astype(np.float32) / np.float32(255.0)
    source_linear = _srgb_to_linear(source_srgb)
    premultiplied = source_linear * matte[..., None]
    matrix = np.asarray(placement.affine, dtype=np.float32)
    size = (int(template.output_width), int(template.output_height))
    warped_premultiplied = cv2.warpAffine(
        premultiplied,
        matrix,
        size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    warped_matte = cv2.warpAffine(
        matte,
        matrix,
        size,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    warped_matte = np.clip(warped_matte, 0.0, 1.0).astype(np.float32)
    background_rgb = np.asarray(template.background_rgb, dtype=np.float32) / 255.0
    background_bgr = background_rgb[::-1]
    background_linear = _srgb_to_linear(background_bgr)
    composed_linear = warped_premultiplied + (
        np.float32(1.0) - warped_matte[..., None]
    ) * background_linear
    output = np.rint(
        np.clip(_linear_to_srgb(composed_linear), 0.0, 1.0) * 255.0
    ).astype(np.uint8)
    output[warped_matte <= 0.0] = np.rint(background_bgr * 255.0).astype(np.uint8)
    return np.ascontiguousarray(output)
