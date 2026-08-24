"""Bounded person-first protection envelope for witness background blur."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from face_algorithm.schemas import CardRegion, FaceBox, ReasonCode
from face_algorithm.witness.contracts import _WitnessBlurError

__all__: list[str] = []


@dataclass(frozen=True, slots=True)
class _WitnessProtectionResult:
    matte: np.ndarray = field(repr=False, compare=False)
    cards: tuple[CardRegion, ...]
    closing_radius: int
    added_area_ratio: float
    protected_area_ratio: float
    diagnostic: str


@dataclass(frozen=True, slots=True)
class _Hole:
    label: int
    area: int
    left: int
    top: int
    width: int
    height: int


def _option(options: Any, name: str, default: float) -> float:
    return float(getattr(options, name, default))


def _closing_radius(
    shape: tuple[int, int], face: FaceBox | None
) -> int:
    height, width = shape
    basis = (
        0.02 * float(face.width)
        if face is not None
        else 0.004 * float(min(width, height))
    )
    return int(np.clip(round(basis), 2, 12))


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
        raise TypeError("person protection requires a contiguous float32 alpha matte")


def _holes(
    core: np.ndarray,
    options: Any,
) -> tuple[np.ndarray, int, list[_Hole]]:
    height, width = core.shape
    image_area = float(height * width)
    background = np.asarray(core == 0, np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        background, connectivity=8
    )
    maximum_single = _option(options, "max_single_hole_area_ratio", 0.05)
    holes: list[_Hole] = []
    for label in range(1, count):
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        component_width = int(stats[label, cv2.CC_STAT_WIDTH])
        component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        touches_edge = (
            left == 0
            or top == 0
            or left + component_width == width
            or top + component_height == height
        )
        if touches_edge:
            continue
        area = int(stats[label, cv2.CC_STAT_AREA])
        if float(area) / image_area > maximum_single:
            continue
        holes.append(
            _Hole(
                label=label,
                area=area,
                left=left,
                top=top,
                width=component_width,
                height=component_height,
            )
        )
    # Smallest-first maximizes the number of repaired segmentation holes. The
    # later rollback is largest-first, matching the approved deterministic rule.
    holes.sort(key=lambda item: (item.area, item.top, item.left, item.label))
    return labels, count, holes


def _fill_holes(
    core: np.ndarray,
    labels: np.ndarray,
    label_count: int,
    holes: list[_Hole],
) -> None:
    """Fill selected labels through one bounded label lookup.

    Keeping one full-image mask per hole can amplify a noisy 16 MP input into
    many gigabytes. Iterating each bounding box can also become quadratic for
    sparse or winding components. A compact label LUT plus one temporary bool
    image keeps both memory and work linear in pixels plus component count.
    """

    selected_labels = np.zeros(label_count, dtype=bool)
    for hole in holes:
        selected_labels[hole.label] = True
    core[selected_labels[labels]] = 1


def _card_core(
    shape: tuple[int, int], card: CardRegion, margin_ratio: float
) -> np.ndarray:
    height, width = shape
    quad = np.asarray([(point.x, point.y) for point in card.corners], np.float32)
    center = np.mean(quad, axis=0)
    expanded = center + (quad - center) * np.float32(1.0 + 2.0 * margin_ratio)
    expanded[:, 0] = np.clip(expanded[:, 0], 0.0, width - 1.0)
    expanded[:, 1] = np.clip(expanded[:, 1], 0.0, height - 1.0)
    mask = np.zeros(shape, np.uint8)
    cv2.fillConvexPoly(mask, np.rint(expanded).astype(np.int32), 1)
    return mask


def _card_sort_key(
    card: CardRegion,
) -> tuple[float, float, float, float, tuple[float, ...]]:
    points = np.asarray([(point.x, point.y) for point in card.corners], np.float32)
    area = abs(float(cv2.contourArea(points)))
    center = np.mean(points, axis=0)
    coordinates = tuple(float(value) for value in points.reshape(-1))
    return (
        -float(card.confidence),
        area,
        float(center[1]),
        float(center[0]),
        coordinates,
    )


def _apply_card_feather(
    protect: np.ndarray,
    card_mask: np.ndarray,
    feather_radius: int,
) -> None:
    protect[card_mask > 0] = np.float32(1.0)
    if feather_radius <= 0 or not np.any(card_mask):
        return
    outside_distance = cv2.distanceTransform(1 - card_mask, cv2.DIST_L2, 3)
    feather = np.clip(
        1.0 - outside_distance / float(feather_radius + 1), 0.0, 1.0
    ).astype(np.float32)
    np.maximum(protect, feather, out=protect)


def _build_witness_protection_mask(
    subject_matte: np.ndarray,
    face: FaceBox | None,
    protection_cards: tuple[CardRegion, ...],
    options: Any,
) -> _WitnessProtectionResult:
    """Build a privacy-bounded clear region around one selected person."""

    _validate_matte(subject_matte)
    if not isinstance(protection_cards, tuple) or not all(
        isinstance(card, CardRegion) for card in protection_cards
    ):
        raise TypeError("protection cards must be a tuple of CardRegion values")
    height, width = subject_matte.shape
    image_area = float(height * width)
    threshold = _option(options, "mask_threshold", 0.50)
    original_core = np.asarray(subject_matte >= threshold, np.uint8)
    original_area = int(np.count_nonzero(original_core))
    if original_area <= 0:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)

    radius = _closing_radius(subject_matte.shape, face)
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    hole_labels, hole_label_count, holes = _holes(original_core, options)
    total_hole_budget = int(
        np.floor(_option(options, "max_total_hole_area_ratio", 0.08) * image_area)
    )
    accepted_holes: list[_Hole] = []
    hole_area = 0
    for hole in holes:
        if hole_area + hole.area <= total_hole_budget:
            accepted_holes.append(hole)
            hole_area += hole.area

    hole_filled = original_core.copy()
    _fill_holes(
        hole_filled,
        hole_labels,
        hole_label_count,
        accepted_holes,
    )
    closed = cv2.morphologyEx(hole_filled, cv2.MORPH_CLOSE, kernel, iterations=1)
    closing_added = np.asarray((closed > 0) & (hole_filled == 0), np.uint8)

    maximum_added = min(
        int(
            np.floor(
                _option(options, "max_envelope_added_image_ratio", 0.08)
                * image_area
            )
        ),
        int(
            np.floor(
                _option(options, "max_envelope_added_subject_ratio", 0.25)
                * float(original_area)
            )
        ),
    )

    def added_area(
        include_closing: bool, selected_holes: list[_Hole]
    ) -> tuple[np.ndarray, int]:
        envelope = original_core.copy()
        _fill_holes(
            envelope,
            hole_labels,
            hole_label_count,
            selected_holes,
        )
        if include_closing:
            envelope[closing_added > 0] = 1
        added = int(np.count_nonzero((envelope > 0) & (original_core == 0)))
        return envelope, added

    include_closing = True
    envelope_core, added = added_area(include_closing, accepted_holes)
    if added > maximum_added:
        include_closing = False
        envelope_core, added = added_area(include_closing, accepted_holes)
    if added > maximum_added:
        rollback_order = sorted(
            accepted_holes,
            key=lambda item: (-item.area, item.top, item.left, item.label),
        )
        dropped_labels: set[int] = set()
        retained_area = sum(item.area for item in accepted_holes)
        for hole in rollback_order:
            if retained_area <= maximum_added:
                break
            dropped_labels.add(hole.label)
            retained_area -= hole.area
        accepted_holes = [
            hole for hole in accepted_holes if hole.label not in dropped_labels
        ]
        envelope_core, added = added_area(include_closing, accepted_holes)
    if added > maximum_added:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)

    person = subject_matte.copy()
    person[envelope_core > 0] = np.float32(1.0)
    np.clip(person, 0.0, 1.0, out=person)
    person_ratio = float(np.mean(person, dtype=np.float64))
    if person_ratio > _option(options, "max_person_protection_ratio", 0.85):
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)

    # Candidate contact uses exactly the closing radius, never a broader halo.
    contact_envelope = cv2.dilate(envelope_core, kernel, iterations=1)
    maximum_card_count = int(
        getattr(
            options,
            "max_protection_card_count",
            getattr(options, "max_card_count", 3),
        )
    )
    maximum_single_card = _option(options, "max_protection_card_area_ratio", 0.12)
    maximum_total_cards = _option(
        options, "max_total_protection_card_area_ratio", 0.20
    )
    minimum_contact = _option(options, "min_protection_card_contact_ratio", 0.20)
    accepted: list[tuple[CardRegion, np.ndarray, int]] = []
    total_card_area = 0
    for card in sorted(protection_cards, key=_card_sort_key):
        raw = _card_core(subject_matte.shape, card, 0.0)
        area = int(np.count_nonzero(raw))
        if area <= 0 or float(area) / image_area > maximum_single_card:
            continue
        contact = float(np.count_nonzero(raw & contact_envelope)) / float(area)
        if contact < minimum_contact:
            continue
        if len(accepted) >= maximum_card_count:
            continue
        if float(total_card_area + area) / image_area > maximum_total_cards:
            continue
        expanded = _card_core(
            subject_matte.shape,
            card,
            _option(options, "card_margin_ratio", 0.02),
        )
        accepted.append((card, expanded, area))
        total_card_area += area

    feather_radius = int(
        np.ceil(
            min(height, width) * _option(options, "feather_radius_ratio", 0.004)
        )
    )
    final_limit = _option(options, "max_final_protection_ratio", 0.92)

    def combine(items: list[tuple[CardRegion, np.ndarray, int]]) -> np.ndarray:
        combined = person.copy()
        for _card, mask, _area in items:
            _apply_card_feather(combined, mask, feather_radius)
        np.clip(combined, 0.0, 1.0, out=combined)
        return np.ascontiguousarray(combined)

    final = combine(accepted)
    # Candidates were confidence-sorted, so dropping from the end is exactly
    # the approved low-confidence-first budget fallback.
    dropped = False
    while accepted and float(np.mean(final, dtype=np.float64)) > final_limit:
        accepted.pop()
        dropped = True
        final = combine(accepted)
    final_ratio = float(np.mean(final, dtype=np.float64))
    if final_ratio > final_limit:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)

    cards = tuple(item[0] for item in accepted)
    diagnostic = (
        "ENHANCED"
        if cards
        else ("DEGRADED" if protection_cards or dropped else "PERSON_ONLY")
    )
    return _WitnessProtectionResult(
        matte=final,
        cards=cards,
        closing_radius=radius,
        added_area_ratio=float(added) / image_area,
        protected_area_ratio=final_ratio,
        diagnostic=diagnostic,
    )
