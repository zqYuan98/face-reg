"""Deterministic, capability-scoped face candidate selection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np

from face_algorithm.core.protocols import LandmarkDetector
from face_algorithm.schemas import FaceBox, ReasonCode

__all__ = [
    "FaceCandidateSelector",
    "FaceSelectionPreparation",
    "FaceSelectionResult",
    "valid_landmark_geometry",
]

_IGNORE_REGION_MIN_COVERAGE = 0.80


@dataclass(frozen=True, slots=True)
class _Candidate:
    raw_box: FaceBox
    box: FaceBox

    @property
    def area(self) -> float:
        return self.box.width * self.box.height


@dataclass(frozen=True, slots=True, repr=False)
class _LandmarkAssessment:
    valid: bool
    landmarks: np.ndarray = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, repr=False)
class FaceSelectionPreparation:
    """Opaque two-stage selection state used by the witness workflow."""

    mode: Literal["exact_one", "dominant_primary"]
    candidate_count: int
    saturated: bool
    candidates: tuple[_Candidate, ...] = field(repr=False)
    assessments: tuple[_LandmarkAssessment | None, ...] = field(repr=False)
    primary_index: int | None
    reason_code: ReasonCode | None
    selection: Any = field(repr=False, compare=False)
    geometry: Any = field(repr=False, compare=False)

    @property
    def raw_box(self) -> FaceBox | None:
        if self.primary_index is None:
            return None
        return self.candidates[self.primary_index].raw_box

    @property
    def box(self) -> FaceBox | None:
        if self.primary_index is None:
            return None
        return self.candidates[self.primary_index].box

    @property
    def landmarks(self) -> np.ndarray | None:
        if self.primary_index is None:
            return None
        assessment = self.assessments[self.primary_index]
        if assessment is None:
            return None
        return assessment.landmarks


@dataclass(frozen=True, slots=True, repr=False)
class FaceSelectionResult:
    """Selection outcome with reusable primary landmarks and one reason code."""

    raw_box: FaceBox | None
    box: FaceBox | None
    landmarks: np.ndarray | None = field(repr=False, compare=False)
    reason_code: ReasonCode | None
    candidate_count: int
    saturated: bool
    secondary_area_ratio: float | None = None

    @property
    def selected(self) -> bool:
        return self.reason_code is None and self.box is not None


class FaceCandidateSelector:
    """Choose one face without weakening strict identity capabilities.

    ``prepare`` finds and validates a temporary primary. ``finalize`` applies
    the secondary-face decision table, optionally after the witness workflow
    has produced card-context regions. ``select`` is the one-shot convenience
    API used by ordinary image pipelines.
    """

    def select(
        self,
        image: np.ndarray,
        candidates: Sequence[FaceBox],
        landmarker: LandmarkDetector,
        selection: Any,
        geometry: Any,
        *,
        ignore_regions: Sequence[object] = (),
    ) -> FaceSelectionResult:
        prepared = self.prepare(
            image,
            candidates,
            landmarker,
            selection,
            geometry,
        )
        return self.finalize(
            image,
            landmarker,
            prepared,
            ignore_regions=ignore_regions,
        )

    def prepare(
        self,
        image: np.ndarray,
        candidates: Sequence[FaceBox],
        landmarker: LandmarkDetector,
        selection: Any,
        geometry: Any,
    ) -> FaceSelectionPreparation:
        mode = getattr(selection, "mode", None)
        candidate_count = _safe_candidate_count(candidates)
        if mode not in ("exact_one", "dominant_primary"):
            return _early_preparation(
                mode="exact_one",
                candidate_count=candidate_count,
                reason_code=ReasonCode.INFERENCE_ERROR,
                selection=selection,
                geometry=geometry,
            )
        if not _valid_image_shape(image):
            return _early_preparation(
                mode=mode,
                candidate_count=candidate_count,
                reason_code=ReasonCode.INFERENCE_ERROR,
                selection=selection,
                geometry=geometry,
            )
        if candidate_count == 0:
            return _early_preparation(
                mode=mode,
                candidate_count=0,
                reason_code=ReasonCode.NO_FACE,
                selection=selection,
                geometry=geometry,
            )

        if mode == "exact_one" and candidate_count >= 2:
            return _early_preparation(
                mode=mode,
                candidate_count=candidate_count,
                reason_code=ReasonCode.MULTIPLE_FACES,
                selection=selection,
                geometry=geometry,
            )

        if mode == "dominant_primary":
            try:
                max_candidates = int(selection.max_face_candidates)
            except (AttributeError, TypeError, ValueError):
                return _early_preparation(
                    mode=mode,
                    candidate_count=candidate_count,
                    reason_code=ReasonCode.INFERENCE_ERROR,
                    selection=selection,
                    geometry=geometry,
                )
            if candidate_count > max_candidates:
                return _early_preparation(
                    mode=mode,
                    candidate_count=candidate_count,
                    reason_code=ReasonCode.MULTIPLE_FACES,
                    selection=selection,
                    geometry=geometry,
                    saturated=True,
                )

        try:
            normalized = _normalize_candidates(image.shape, candidates)
        except (AttributeError, TypeError, ValueError):
            return _early_preparation(
                mode=mode,
                candidate_count=candidate_count,
                reason_code=ReasonCode.INFERENCE_ERROR,
                selection=selection,
                geometry=geometry,
            )
        if not normalized:
            reason = (
                ReasonCode.FACE_CROPPED
                if mode == "exact_one"
                else ReasonCode.NO_FACE
            )
            return _early_preparation(
                mode=mode,
                candidate_count=candidate_count,
                reason_code=reason,
                selection=selection,
                geometry=geometry,
            )

        if mode == "dominant_primary":
            normalized = tuple(
                sorted(
                    normalized,
                    key=lambda item: _candidate_sort_key(item, image.shape),
                )
            )

        assessments: list[_LandmarkAssessment | None] = [None] * len(normalized)
        primary_index: int | None = None
        for index, candidate in enumerate(normalized):
            assessment, error = _assess_landmarks(
                image,
                candidate.box,
                landmarker,
                geometry,
            )
            if error is not None:
                return FaceSelectionPreparation(
                    mode=mode,
                    candidate_count=candidate_count,
                    saturated=False,
                    candidates=normalized,
                    assessments=tuple(assessments),
                    primary_index=None,
                    reason_code=error,
                    selection=selection,
                    geometry=geometry,
                )
            assert assessment is not None
            assessments[index] = assessment
            if assessment.valid:
                primary_index = index
                break
            if mode == "exact_one":
                return FaceSelectionPreparation(
                    mode=mode,
                    candidate_count=candidate_count,
                    saturated=False,
                    candidates=normalized,
                    assessments=tuple(assessments),
                    primary_index=index,
                    reason_code=ReasonCode.LANDMARK_FAILED,
                    selection=selection,
                    geometry=geometry,
                )

        if primary_index is None:
            return FaceSelectionPreparation(
                mode=mode,
                candidate_count=candidate_count,
                saturated=False,
                candidates=normalized,
                assessments=tuple(assessments),
                primary_index=None,
                reason_code=ReasonCode.LANDMARK_FAILED,
                selection=selection,
                geometry=geometry,
            )
        return FaceSelectionPreparation(
            mode=mode,
            candidate_count=candidate_count,
            saturated=False,
            candidates=normalized,
            assessments=tuple(assessments),
            primary_index=primary_index,
            reason_code=None,
            selection=selection,
            geometry=geometry,
        )

    def finalize(
        self,
        image: np.ndarray,
        landmarker: LandmarkDetector,
        prepared: FaceSelectionPreparation,
        *,
        ignore_regions: Sequence[object] = (),
    ) -> FaceSelectionResult:
        if not isinstance(prepared, FaceSelectionPreparation):
            raise TypeError("prepared must be a FaceSelectionPreparation")
        if prepared.reason_code is not None or prepared.primary_index is None:
            return _result_from_preparation(prepared)

        primary = prepared.candidates[prepared.primary_index]
        primary_assessment = prepared.assessments[prepared.primary_index]
        if primary_assessment is None or not primary_assessment.valid:
            return _result_from_preparation(
                prepared,
                reason_code=ReasonCode.INFERENCE_ERROR,
            )
        if prepared.mode == "exact_one":
            return _result_from_preparation(prepared)

        try:
            ignore_ratio = float(
                prepared.selection.ignore_secondary_area_ratio
            )
            ambiguous_ratio = float(
                prepared.selection.ambiguous_secondary_area_ratio
            )
            min_confidence = float(
                prepared.selection.secondary_min_confidence
            )
        except (AttributeError, TypeError, ValueError):
            return _result_from_preparation(
                prepared,
                reason_code=ReasonCode.INFERENCE_ERROR,
            )

        maximum_secondary_ratio: float | None = None
        for index, candidate in enumerate(prepared.candidates):
            if index == prepared.primary_index:
                continue
            ratio = candidate.area / primary.area
            maximum_secondary_ratio = (
                ratio
                if maximum_secondary_ratio is None
                else max(maximum_secondary_ratio, ratio)
            )
            if ratio < ignore_ratio - 1e-9 or _covered_by_ignore_region(
                candidate.box,
                ignore_regions,
            ):
                continue

            assessment = prepared.assessments[index]
            if assessment is None:
                assessment, error = _assess_landmarks(
                    image,
                    candidate.box,
                    landmarker,
                    prepared.geometry,
                )
                if error is not None:
                    return _result_from_preparation(
                        prepared,
                        reason_code=error,
                        secondary_area_ratio=maximum_secondary_ratio,
                    )
                assert assessment is not None

            if ratio < ambiguous_ratio - 1e-9:
                ambiguous = (
                    candidate.box.confidence >= min_confidence
                    and assessment.valid
                )
            else:
                ambiguous = (
                    assessment.valid
                    or candidate.box.confidence >= min_confidence
                )
            if ambiguous:
                return _result_from_preparation(
                    prepared,
                    reason_code=ReasonCode.MULTIPLE_FACES,
                    secondary_area_ratio=maximum_secondary_ratio,
                )

        return _result_from_preparation(
            prepared,
            secondary_area_ratio=maximum_secondary_ratio,
        )


def _safe_candidate_count(candidates: object) -> int:
    try:
        return len(candidates)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1


def _valid_image_shape(image: object) -> bool:
    return bool(
        isinstance(image, np.ndarray)
        and image.ndim == 3
        and image.shape[0] > 0
        and image.shape[1] > 0
    )


def _early_preparation(
    *,
    mode: Literal["exact_one", "dominant_primary"],
    candidate_count: int,
    reason_code: ReasonCode,
    selection: Any,
    geometry: Any,
    saturated: bool = False,
) -> FaceSelectionPreparation:
    return FaceSelectionPreparation(
        mode=mode,
        candidate_count=max(0, candidate_count),
        saturated=saturated,
        candidates=(),
        assessments=(),
        primary_index=None,
        reason_code=reason_code,
        selection=selection,
        geometry=geometry,
    )


def _result_from_preparation(
    prepared: FaceSelectionPreparation,
    *,
    reason_code: ReasonCode | None = None,
    secondary_area_ratio: float | None = None,
) -> FaceSelectionResult:
    effective_reason = (
        prepared.reason_code if reason_code is None else reason_code
    )
    landmarks = prepared.landmarks
    return FaceSelectionResult(
        raw_box=prepared.raw_box,
        box=prepared.box,
        landmarks=landmarks,
        reason_code=effective_reason,
        candidate_count=prepared.candidate_count,
        saturated=prepared.saturated,
        secondary_area_ratio=secondary_area_ratio,
    )


def _coerce_face_box(value: object) -> FaceBox:
    if isinstance(value, FaceBox):
        return value
    confidence = getattr(value, "confidence", getattr(value, "score", 1.0))
    return FaceBox(
        x1=float(getattr(value, "x1")),
        y1=float(getattr(value, "y1")),
        x2=float(getattr(value, "x2")),
        y2=float(getattr(value, "y2")),
        confidence=float(confidence),
    )


def _clip_box(box: FaceBox, shape: tuple[int, ...]) -> FaceBox | None:
    height, width = shape[:2]
    x1 = float(np.clip(box.x1, 0.0, float(width)))
    y1 = float(np.clip(box.y1, 0.0, float(height)))
    x2 = float(np.clip(box.x2, 0.0, float(width)))
    y2 = float(np.clip(box.y2, 0.0, float(height)))
    if x2 <= x1 or y2 <= y1:
        return None
    return FaceBox(
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        confidence=box.confidence,
    )


def _normalize_candidates(
    shape: tuple[int, ...],
    candidates: Sequence[FaceBox],
) -> tuple[_Candidate, ...]:
    normalized: list[_Candidate] = []
    for value in candidates:
        raw_box = _coerce_face_box(value)
        clipped = _clip_box(raw_box, shape)
        if clipped is not None:
            normalized.append(_Candidate(raw_box=raw_box, box=clipped))
    return tuple(normalized)


def _candidate_sort_key(
    candidate: _Candidate,
    shape: tuple[int, ...],
) -> tuple[float, ...]:
    height, width = map(float, shape[:2])
    box = candidate.box
    center_x = ((box.x1 + box.x2) * 0.5) / width
    center_y = ((box.y1 + box.y2) * 0.5) / height
    center_distance = (center_x - 0.5) ** 2 + (center_y - 0.5) ** 2
    return (
        -candidate.area,
        -box.confidence,
        center_distance,
        box.x1 / width,
        box.y1 / height,
        box.x2 / width,
        box.y2 / height,
    )


def _assess_landmarks(
    image: np.ndarray,
    box: FaceBox,
    landmarker: LandmarkDetector,
    geometry: Any,
) -> tuple[_LandmarkAssessment | None, ReasonCode | None]:
    try:
        landmarks = np.asarray(
            landmarker.detect(image, box),
            dtype=np.float32,
        )
    except Exception:
        return None, ReasonCode.INFERENCE_ERROR
    if landmarks.shape != (5, 2) or not np.all(np.isfinite(landmarks)):
        return None, ReasonCode.INFERENCE_ERROR
    landmarks = landmarks.copy()
    landmarks.setflags(write=False)
    try:
        valid = valid_landmark_geometry(landmarks, box, geometry)
    except (AttributeError, TypeError, ValueError):
        return None, ReasonCode.INFERENCE_ERROR
    return _LandmarkAssessment(valid=valid, landmarks=landmarks), None


def valid_landmark_geometry(
    landmarks: np.ndarray,
    box: FaceBox,
    geometry: Any,
) -> bool:
    """Validate only five-point facial geometry, not pose or image quality."""

    if landmarks.shape != (5, 2) or not np.all(np.isfinite(landmarks)):
        return False
    tolerance_x = box.width * float(geometry.bbox_tolerance_ratio)
    tolerance_y = box.height * float(geometry.bbox_tolerance_ratio)
    if bool(geometry.require_points_inside_bbox) and (
        np.any(landmarks[:, 0] < box.x1 - tolerance_x)
        or np.any(landmarks[:, 0] > box.x2 + tolerance_x)
        or np.any(landmarks[:, 1] < box.y1 - tolerance_y)
        or np.any(landmarks[:, 1] > box.y2 + tolerance_y)
    ):
        return False
    left_eye, right_eye, nose, left_mouth, right_mouth = landmarks
    if not (left_eye[0] < right_eye[0] and left_mouth[0] < right_mouth[0]):
        return False
    if not (
        max(left_eye[1], right_eye[1])
        < nose[1]
        < min(left_mouth[1], right_mouth[1])
    ):
        return False
    if (
        (right_eye[0] - left_eye[0]) / box.width
        < float(geometry.min_eye_distance_ratio)
    ):
        return False
    if (
        (right_mouth[0] - left_mouth[0]) / box.width
        < float(geometry.min_mouth_width_ratio)
    ):
        return False
    if (
        abs(right_eye[1] - left_eye[1]) / box.height
        > float(geometry.max_eye_vertical_difference_ratio)
    ):
        return False
    return True


def _covered_by_ignore_region(
    box: FaceBox,
    ignore_regions: Sequence[object],
) -> bool:
    if not ignore_regions:
        return False
    box_polygon = np.asarray(
        [
            (box.x1, box.y1),
            (box.x2, box.y1),
            (box.x2, box.y2),
            (box.x1, box.y2),
        ],
        dtype=np.float32,
    )
    box_area = box.width * box.height
    for region in ignore_regions:
        polygon = _region_polygon(region)
        if polygon is None:
            continue
        try:
            intersection_area, _ = cv2.intersectConvexConvex(
                box_polygon,
                polygon,
            )
        except cv2.error:
            continue
        coverage = max(0.0, float(intersection_area)) / box_area
        if coverage >= _IGNORE_REGION_MIN_COVERAGE - 1e-6:
            return True
    return False


def _region_polygon(region: object) -> np.ndarray | None:
    if isinstance(region, FaceBox):
        points = np.asarray(
            [
                (region.x1, region.y1),
                (region.x2, region.y1),
                (region.x2, region.y2),
                (region.x1, region.y2),
            ],
            dtype=np.float32,
        )
    else:
        source = getattr(region, "corners", region)
        if isinstance(source, Sequence) and len(source) == 4 and all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in source
        ):
            x1, y1, x2, y2 = map(float, source)
            points = np.asarray(
                [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                dtype=np.float32,
            )
        else:
            try:
                points = np.asarray(
                    [_point_coordinates(point) for point in source],  # type: ignore[union-attr]
                    dtype=np.float32,
                )
            except (AttributeError, IndexError, TypeError, ValueError):
                return None
    if (
        points.ndim != 2
        or points.shape[0] < 3
        or points.shape[1] != 2
        or not np.all(np.isfinite(points))
    ):
        return None
    hull = cv2.convexHull(points).reshape(-1, 2)
    if hull.shape[0] < 3 or cv2.contourArea(hull) <= 0:
        return None
    return np.asarray(hull, dtype=np.float32)


def _point_coordinates(point: object) -> tuple[float, float]:
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(getattr(point, "x")), float(getattr(point, "y"))
    return float(point[0]), float(point[1])  # type: ignore[index]
