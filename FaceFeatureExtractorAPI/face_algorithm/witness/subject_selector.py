"""Deterministic person-component selection for witness background blur."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from face_algorithm.schemas import FaceBox, ReasonCode
from face_algorithm.witness.contracts import (
    WitnessMattingQuality,
    _WitnessBlurError,
)

__all__: list[str] = []


@dataclass(frozen=True, slots=True)
class _WitnessSubjectSelection:
    matte: np.ndarray = field(repr=False, compare=False)
    quality: WitnessMattingQuality
    component_count: int
    anchored: bool


@dataclass(frozen=True, slots=True)
class _Component:
    label: int
    area: int
    centroid_x: float
    centroid_y: float
    left: int
    top: int
    width: int
    height: int
    soft_label: int
    soft_area: int


def _option(options: Any, name: str, default: float) -> float:
    return float(getattr(options, name, default))


def _validate_matte(matte: np.ndarray) -> None:
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


def _face_slices(face: FaceBox, shape: tuple[int, int]) -> tuple[slice, slice]:
    height, width = shape
    x1 = max(0, min(width, int(np.floor(face.x1))))
    y1 = max(0, min(height, int(np.floor(face.y1))))
    x2 = max(0, min(width, int(np.ceil(face.x2))))
    y2 = max(0, min(height, int(np.ceil(face.y2))))
    if x2 <= x1 or y2 <= y1:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
    return slice(y1, y2), slice(x1, x2)


def _components(
    matte: np.ndarray,
    options: Any,
) -> tuple[np.ndarray, list[_Component]]:
    height, width = matte.shape
    image_area = float(height * width)
    hard = np.asarray(matte >= _option(options, "mask_threshold", 0.50), np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        hard, connectivity=8
    )
    soft = np.asarray(
        matte >= _option(options, "subject_edge_alpha", 0.15), np.uint8
    )
    soft_count, soft_labels, soft_stats, _ = cv2.connectedComponentsWithStats(
        soft, connectivity=8
    )
    minimum_area = _option(options, "min_subject_area_ratio", 0.03) * image_area
    maximum_soft_area = (
        _option(options, "max_subject_area_ratio", 0.92) * image_area
    )
    reliable: list[_Component] = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < minimum_area:
            continue
        overlaps = soft_labels[labels == label]
        overlap_counts = np.bincount(overlaps, minlength=soft_count)
        if overlap_counts.size <= 1:
            continue
        overlap_counts[0] = 0
        soft_label = int(np.argmax(overlap_counts))
        if overlap_counts[soft_label] <= 0:
            continue
        soft_area = int(soft_stats[soft_label, cv2.CC_STAT_AREA])
        if soft_area > maximum_soft_area:
            continue
        reliable.append(
            _Component(
                label=label,
                area=area,
                centroid_x=float(centroids[label, 0]),
                centroid_y=float(centroids[label, 1]),
                left=int(stats[label, cv2.CC_STAT_LEFT]),
                top=int(stats[label, cv2.CC_STAT_TOP]),
                width=int(stats[label, cv2.CC_STAT_WIDTH]),
                height=int(stats[label, cv2.CC_STAT_HEIGHT]),
                soft_label=soft_label,
                soft_area=soft_area,
            )
        )
    reliable.sort(
        key=lambda item: (
            -item.area,
            item.centroid_y,
            item.centroid_x,
            item.top,
            item.left,
            item.height,
            item.width,
        )
    )
    return labels, reliable


def _analyze_witness_subject(
    matte: np.ndarray,
    face: FaceBox | None,
    options: Any,
) -> _WitnessSubjectSelection:
    """Select one reliable person, anchored by a face when available."""

    _validate_matte(matte)
    height, width = matte.shape
    labels, reliable = _components(matte, options)
    if not reliable:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)

    face_mean = 0.0
    face_coverage = 0.0
    anchored = face is not None
    if face is not None:
        face_y, face_x = _face_slices(face, matte.shape)
        face_pixels = int((face_y.stop - face_y.start) * (face_x.stop - face_x.start))
        anchor_minimum = _option(options, "face_component_min_coverage", 0.50)
        anchored_components: list[tuple[_Component, float]] = []
        for component in reliable:
            coverage = float(
                np.count_nonzero(labels[face_y, face_x] == component.label)
            ) / float(face_pixels)
            if coverage >= anchor_minimum:
                anchored_components.append((component, coverage))
        if len(anchored_components) != 1:
            raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
        selected, face_coverage = anchored_components[0]
    else:
        selected = reliable[0]

    secondaries = [item for item in reliable if item.label != selected.label]
    if secondaries:
        ratio = float(secondaries[0].area) / float(selected.area)
        if ratio >= _option(options, "secondary_subject_area_ratio", 0.35):
            raise _WitnessBlurError(ReasonCode.MULTIPLE_FACES)

    # Keep the selected soft component, while explicitly removing every other
    # reliable hard core in case low alpha haze joined two people together.
    soft = np.asarray(
        matte >= _option(options, "subject_edge_alpha", 0.15), np.uint8
    )
    _soft_count, soft_labels, _stats, _centroids = cv2.connectedComponentsWithStats(
        soft, connectivity=8
    )
    cleaned = np.where(
        soft_labels == selected.soft_label, matte, np.float32(0.0)
    ).astype(np.float32, copy=False)
    for component in secondaries:
        cleaned[labels == component.label] = np.float32(0.0)
    cleaned = np.ascontiguousarray(cleaned)

    if face is not None:
        face_values = cleaned[face_y, face_x]
        face_mean = float(np.mean(face_values, dtype=np.float64))
        face_coverage = float(
            np.count_nonzero(
                face_values >= _option(options, "mask_threshold", 0.50)
            )
        ) / float(face_values.size)

    return _WitnessSubjectSelection(
        matte=cleaned,
        quality=WitnessMattingQuality(
            face_mean=face_mean,
            face_coverage=face_coverage,
            subject_area_ratio=float(selected.area) / float(height * width),
        ),
        component_count=len(reliable),
        anchored=anchored,
    )


def _select_witness_subject_matte(
    matte: np.ndarray,
    face: FaceBox,
    options: Any,
) -> tuple[np.ndarray, WitnessMattingQuality]:
    """Compatibility wrapper for callers that require a face anchor."""

    selection = _analyze_witness_subject(matte, face, options)
    return selection.matte, selection.quality
