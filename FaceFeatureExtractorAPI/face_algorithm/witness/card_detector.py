"""Deterministic, conservative quadrilateral detection for held cards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from face_algorithm.schemas import CardRegion, FaceBox, LandmarkPoint, ReasonCode

__all__: list[str] = []


@dataclass(frozen=True, slots=True)
class _CardDetectionResult:
    cards: tuple[CardRegion, ...]
    candidate_count: int
    reason_code: ReasonCode | None
    protection_cards: tuple[CardRegion, ...] = ()

    def __post_init__(self) -> None:
        # Internal test seams and older owned detectors that only return trusted
        # cards remain safe: every trusted card is also a protection candidate.
        if self.cards and not self.protection_cards:
            object.__setattr__(self, "protection_cards", self.cards)


@dataclass(frozen=True, slots=True, repr=False)
class _Candidate:
    quad: np.ndarray
    area: float
    confidence: float
    cropped: bool
    too_small: bool
    contact: float = 0.0


def _order_quad(points: np.ndarray) -> np.ndarray:
    quad = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = np.mean(quad, axis=0)
    angles = np.arctan2(quad[:, 1] - center[1], quad[:, 0] - center[0])
    quad = quad[np.argsort(angles)]
    start = int(np.argmin(np.sum(quad, axis=1)))
    return np.ascontiguousarray(np.roll(quad, -start, axis=0))


def _quad_metrics(quad: np.ndarray) -> tuple[float, float, float, float]:
    edges = np.roll(quad, -1, axis=0) - quad
    lengths = np.linalg.norm(edges, axis=1)
    short = float((lengths[1] + lengths[3]) * 0.5)
    long = float((lengths[0] + lengths[2]) * 0.5)
    if short > long:
        short, long = long, short
    deviations: list[float] = []
    for index in range(4):
        before = quad[index - 1] - quad[index]
        after = quad[(index + 1) % 4] - quad[index]
        denominator = float(np.linalg.norm(before) * np.linalg.norm(after))
        if denominator <= 1e-6:
            return 0.0, 0.0, 0.0, 90.0
        cosine = float(np.clip(np.dot(before, after) / denominator, -1.0, 1.0))
        deviations.append(abs(float(np.degrees(np.arccos(cosine))) - 90.0))
    return short, long, (long / short if short > 1e-6 else 0.0), max(deviations)


def _quad_overlap(first: np.ndarray, second: np.ndarray) -> float:
    area_first = abs(float(cv2.contourArea(first)))
    area_second = abs(float(cv2.contourArea(second)))
    if area_first <= 0.0 or area_second <= 0.0:
        return 0.0
    intersection, _polygon = cv2.intersectConvexConvex(first, second)
    union = area_first + area_second - float(intersection)
    return float(intersection) / union if union > 0.0 else 0.0


def _candidate_is_covered(
    candidate: _Candidate,
    protector: _Candidate,
    *,
    overlap_threshold: float,
    protection_margin_ratio: float,
    output_scale: float = 1.0,
) -> bool:
    if _quad_overlap(candidate.quad, protector.quad) <= overlap_threshold:
        return False
    candidate_quad = candidate.quad * np.float32(output_scale)
    protector_quad = protector.quad * np.float32(output_scale)
    center = np.mean(protector_quad, axis=0)
    expanded = center + (protector_quad - center) * np.float32(
        1.0 + 2.0 * protection_margin_ratio
    )
    candidate_pixels = np.rint(candidate_quad).astype(np.int32)
    protector_pixels = np.rint(expanded).astype(np.int32)
    combined = np.concatenate((candidate_pixels, protector_pixels), axis=0)
    origin = np.min(combined, axis=0)
    maximum = np.max(combined, axis=0)
    local_width, local_height = (maximum - origin + 1).tolist()
    if local_width <= 0 or local_height <= 0:
        return False
    candidate_mask = np.zeros((local_height, local_width), np.uint8)
    protector_mask = np.zeros_like(candidate_mask)
    cv2.fillConvexPoly(candidate_mask, candidate_pixels - origin, 1)
    cv2.fillConvexPoly(protector_mask, protector_pixels - origin, 1)
    return not np.any((candidate_mask > 0) & (protector_mask == 0))


def _nms(
    candidates: list[_Candidate],
    threshold: float,
    protection_margin_ratio: float,
    output_scale: float = 1.0,
) -> list[_Candidate]:
    """Deduplicate only quads already protected by a retained candidate."""

    kept: list[_Candidate] = []
    for candidate in sorted(candidates, key=lambda item: (-item.confidence, -item.area)):
        if not any(
            _candidate_is_covered(
                candidate,
                other,
                overlap_threshold=threshold,
                protection_margin_ratio=protection_margin_ratio,
                output_scale=output_scale,
            )
            for other in kept
        ):
            kept.append(candidate)
    return kept


def _regions_from_candidates(
    candidates: list[_Candidate],
    *,
    detection_scale: float,
    original_width: int,
    original_height: int,
) -> tuple[CardRegion, ...]:
    return tuple(
        CardRegion(
            corners=tuple(
                LandmarkPoint(x=float(point[0]), y=float(point[1]))
                if detection_scale == 1.0
                else LandmarkPoint(
                    x=float(
                        np.clip(
                            point[0] / detection_scale,
                            0.0,
                            original_width - 1.0,
                        )
                    ),
                    y=float(
                        np.clip(
                            point[1] / detection_scale,
                            0.0,
                            original_height - 1.0,
                        )
                    ),
                )
                for point in candidate.quad
            ),
            confidence=candidate.confidence,
        )
        for candidate in sorted(
            candidates,
            key=lambda item: (
                float(np.mean(item.quad[:, 1])),
                float(np.mean(item.quad[:, 0])),
                -item.confidence,
            ),
        )
    )


def _detect_card_regions(
    image: np.ndarray,
    face: FaceBox,
    subject_matte: np.ndarray,
    options: Any,
) -> _CardDetectionResult:
    """Return every credible card or one conservative failure reason."""

    if not (
        isinstance(image, np.ndarray)
        and image.dtype == np.uint8
        and image.ndim == 3
        and image.shape[2] == 3
        and image.flags.c_contiguous
        and isinstance(subject_matte, np.ndarray)
        and subject_matte.dtype == np.float32
        and subject_matte.shape == image.shape[:2]
        and subject_matte.flags.c_contiguous
        and np.all(np.isfinite(subject_matte))
        and float(subject_matte.min()) >= 0.0
        and float(subject_matte.max()) <= 1.0
    ):
        raise TypeError("card detector input contract violated")
    original_height, original_width = image.shape[:2]
    max_dimension = int(options.card_detection_max_dimension)
    detection_scale = min(
        1.0,
        float(max_dimension) / float(max(original_height, original_width)),
    )
    if detection_scale < 1.0:
        detection_width = max(1, int(round(original_width * detection_scale)))
        detection_height = max(1, int(round(original_height * detection_scale)))
        image = np.ascontiguousarray(
            cv2.resize(
                image,
                (detection_width, detection_height),
                interpolation=cv2.INTER_AREA,
            )
        )
        subject_matte = np.ascontiguousarray(
            cv2.resize(
                subject_matte,
                (detection_width, detection_height),
                interpolation=cv2.INTER_LINEAR,
            ).astype(np.float32, copy=False)
        )
        face = FaceBox(
            x1=face.x1 * detection_scale,
            y1=face.y1 * detection_scale,
            x2=face.x2 * detection_scale,
            y2=face.y2 * detection_scale,
            confidence=face.confidence,
        )
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    median = float(np.median(gray))
    lower = int(np.clip(0.55 * median, 20, 180))
    upper = int(np.clip(1.45 * median, lower + 20, 250))
    edges = cv2.Canny(gray, lower, upper, L2gradient=True)
    close_size = max(3, int(round(min(height, width) * 0.012)) | 1)
    close_size = min(close_size, 15)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)
    edge_contours, _hierarchy = cv2.findContours(
        closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )
    # A hand can occlude two or more card edges, especially on a borderless
    # white ID card. Add a conservative low-saturation/light-region proposal
    # stream; all proposals still pass the same face-relative, contact and
    # rectangularity gates below.
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    value_floor = int(max(210.0, np.percentile(hsv[..., 2], 75.0)))
    light = np.asarray(
        (hsv[..., 2] >= value_floor) & (hsv[..., 1] <= 80), np.uint8
    )
    light = cv2.morphologyEx(
        light,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
    )
    light = cv2.morphologyEx(
        light,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)),
    )
    light_contours, _ = cv2.findContours(
        light, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
    )
    contours = tuple(edge_contours) + tuple(light_contours)

    subject = np.asarray(subject_matte >= float(options.subject_edge_alpha), np.uint8)
    contact_radius = int(
        np.ceil(min(height, width) * float(options.card_contact_dilation_ratio))
    )
    if contact_radius > 0:
        contact_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (contact_radius * 2 + 1, contact_radius * 2 + 1)
        )
        subject_contact = cv2.dilate(subject, contact_kernel)
    else:
        subject_contact = subject

    image_area = float(height * width)
    face_area = float(face.width * face.height)
    # A clipped thick border produces an apparent contour a few pixels inside
    # the frame. Include the close-kernel radius so that such a card cannot be
    # mistaken for a complete quadrilateral.
    edge_margin_x = max(1.0, width * float(options.card_edge_margin_ratio)) + close_size
    edge_margin_y = max(1.0, height * float(options.card_edge_margin_ratio)) + close_size
    face_quad = np.asarray(
        ((face.x1, face.y1), (face.x2, face.y1), (face.x2, face.y2), (face.x1, face.y2)),
        dtype=np.float32,
    )
    candidates: list[_Candidate] = []

    for contour in contours:
        contour_area = abs(float(cv2.contourArea(contour)))
        if contour_area < 16.0:
            continue
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0.0:
            continue
        approximation = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        used_approximation = len(approximation) == 4 and cv2.isContourConvex(approximation)
        if used_approximation:
            quad = _order_quad(approximation[:, 0, :])
        else:
            rectangle = cv2.minAreaRect(contour)
            if min(rectangle[1]) <= 1.0:
                continue
            quad = _order_quad(cv2.boxPoints(rectangle))
        quad_area = abs(float(cv2.contourArea(quad)))
        if quad_area <= 0.0:
            continue
        rectangle = cv2.minAreaRect(quad)
        rectangle_area = float(rectangle[1][0] * rectangle[1][1])
        rectangularity = min(1.0, contour_area / max(rectangle_area, 1e-6))
        short, long, aspect, angle_deviation = _quad_metrics(quad)
        if (
            aspect < float(options.min_card_aspect_ratio)
            or aspect > float(options.max_card_aspect_ratio)
            or rectangularity < float(options.min_card_rectangularity)
            or angle_deviation > float(options.max_card_angle_deviation_degrees)
        ):
            continue

        center_x, center_y = np.mean(quad, axis=0)
        overlap_face = _quad_overlap(quad, face_quad)
        center_offset = abs(float(center_x) - (face.x1 + face.x2) * 0.5)
        if (
            center_y < face.y1 + face.height * 0.70
            or overlap_face > 0.05
            or center_offset
            > face.width
            * float(options.max_card_center_offset_to_face_width_ratio)
        ):
            continue
        # Face-relative width is defined by the enclosing rotated rectangle;
        # this remains stable for a mildly perspective quadrilateral and makes
        # configured inclusive boundaries auditable.
        card_width = max(float(rectangle[1][0]), float(rectangle[1][1]))
        width_ratio = card_width / face.width
        image_ratio = quad_area / image_area
        face_ratio = quad_area / face_area
        cropped = bool(
            np.any(quad[:, 0] <= edge_margin_x)
            or np.any(quad[:, 1] <= edge_margin_y)
            or np.any(quad[:, 0] >= width - 1.0 - edge_margin_x)
            or np.any(quad[:, 1] >= height - 1.0 - edge_margin_y)
        )
        tolerance = 1e-6
        original_short = short / detection_scale
        # Rectangles far below every face-relative whole-card scale are most
        # often card logos/chips, garment texture or screen widgets. They are
        # not evidence that a complete card was found-but-too-small.
        if (
            original_short < float(options.min_card_short_side_pixels) * 0.50
            or width_ratio
            < float(options.min_card_width_to_face_ratio) * 0.50
            or face_ratio
            < float(options.min_card_area_to_face_ratio) * 0.25
        ):
            continue
        too_small = bool(
            original_short + tolerance < float(options.min_card_short_side_pixels)
            or image_ratio + tolerance < float(options.min_card_area_ratio)
            or width_ratio + tolerance < float(options.min_card_width_to_face_ratio)
            or face_ratio + tolerance < float(options.min_card_area_to_face_ratio)
        )
        too_large = bool(
            image_ratio - tolerance > float(options.max_card_area_ratio)
            or width_ratio - tolerance > float(options.max_card_width_to_face_ratio)
            or face_ratio - tolerance > float(options.max_card_area_to_face_ratio)
        )
        if too_large:
            continue
        polygon = np.zeros((height, width), np.uint8)
        cv2.fillConvexPoly(polygon, np.rint(quad).astype(np.int32), 1)
        contact = float(np.count_nonzero(polygon & subject_contact)) / max(
            1.0, float(np.count_nonzero(polygon))
        )
        if contact <= 0.015:
            continue

        # Rectangularity already passed the hard configured gate. Retain its
        # absolute evidence here rather than renormalizing a valid boundary
        # candidate almost to zero (hand occlusion commonly lands near it).
        rect_score = float(np.clip(rectangularity, 0.0, 1.0))
        angle_score = np.clip(
            1.0
            - angle_deviation
            / max(1e-6, float(options.max_card_angle_deviation_degrees)),
            0.0,
            1.0,
        )
        aspect_middle = (
            float(options.min_card_aspect_ratio)
            + float(options.max_card_aspect_ratio)
        ) * 0.5
        aspect_span = max(
            0.01,
            (float(options.max_card_aspect_ratio) - float(options.min_card_aspect_ratio))
            * 0.5,
        )
        aspect_score = float(np.clip(1.0 - abs(aspect - aspect_middle) / (aspect_span * 2.0), 0.0, 1.0))
        confidence = float(
            np.clip(
                0.38 * rect_score
                + 0.24 * angle_score
                + 0.18 * aspect_score
                + 0.20 * min(1.0, contact * 2.0)
                + (0.03 if used_approximation else 0.0),
                0.0,
                1.0,
            )
        )
        candidates.append(
            _Candidate(
                quad=quad,
                area=quad_area,
                confidence=confidence,
                cropped=cropped,
                too_small=too_small,
                contact=contact,
            )
        )

    candidate_count = len(candidates)
    sized = [candidate for candidate in candidates if not candidate.too_small]
    threshold = float(options.min_card_candidate_confidence)
    ambiguity_floor = threshold - float(options.card_ambiguity_margin)
    trusted = [candidate for candidate in sized if candidate.confidence >= threshold]
    trusted = _nms(
        trusted,
        float(options.card_nms_iou_threshold),
        float(options.card_margin_ratio),
        1.0 / detection_scale,
    )

    # Protection candidates deliberately use a lower confidence gate than the
    # public trusted-card list, but remain bounded by person contact, count and
    # area. Runtime performs the final 92% soft-protection budget check.
    protection_min_contact = float(
        getattr(options, "min_protection_card_contact_ratio", 0.20)
    )
    protection_max_area = float(
        getattr(options, "max_protection_card_area_ratio", 0.12)
    )
    protection_max_total = float(
        getattr(options, "max_total_protection_card_area_ratio", 0.20)
    )
    protection_max_count = int(
        getattr(options, "max_protection_card_count", options.max_card_count)
    )
    protection_pool = [
        candidate
        for candidate in candidates
        if candidate.contact >= protection_min_contact
        and candidate.area / image_area <= protection_max_area
    ]
    protection_pool = _nms(
        protection_pool,
        float(options.card_nms_iou_threshold),
        float(options.card_margin_ratio),
        1.0 / detection_scale,
    )
    protection_selected: list[_Candidate] = []
    protection_area = 0.0
    for candidate in sorted(
        protection_pool,
        key=lambda item: (
            -item.confidence,
            item.area,
            float(np.mean(item.quad[:, 1])),
            float(np.mean(item.quad[:, 0])),
        ),
    ):
        if len(protection_selected) >= protection_max_count:
            break
        if (protection_area + candidate.area) / image_area > protection_max_total:
            continue
        protection_selected.append(candidate)
        protection_area += candidate.area
    protection_regions = _regions_from_candidates(
        protection_selected,
        detection_scale=detection_scale,
        original_width=original_width,
        original_height=original_height,
    )

    # Suppress a secondary contour only after proving that a final trusted
    # card (including its configured protection margin) covers every raster
    # pixel. Partial-overlap candidates remain actionable evidence.
    unresolved = [
        candidate
        for candidate in candidates
        if not (
            all(candidate is not protector for protector in trusted)
            and any(
                _candidate_is_covered(
                    candidate,
                    protector,
                    overlap_threshold=float(options.card_nms_iou_threshold),
                    protection_margin_ratio=float(options.card_margin_ratio),
                    output_scale=1.0 / detection_scale,
                )
                for protector in trusted
            )
        )
    ]
    trusted_public = [candidate for candidate in trusted if not candidate.cropped]
    trusted_regions = _regions_from_candidates(
        trusted_public,
        detection_scale=detection_scale,
        original_width=original_width,
        original_height=original_height,
    )
    if any(candidate.cropped for candidate in unresolved):
        return _CardDetectionResult(
            cards=trusted_regions,
            candidate_count=candidate_count,
            reason_code=ReasonCode.CARD_CROPPED,
            protection_cards=protection_regions,
        )
    # Preserve unresolved evidence for internal diagnostics. Runtime treats
    # every CARD_* status as an optional-enhancement miss and falls back to the
    # bounded person envelope; it never exposes an unaccepted card as trusted.
    if any(candidate.too_small for candidate in unresolved):
        return _CardDetectionResult(
            cards=trusted_regions,
            candidate_count=candidate_count,
            reason_code=ReasonCode.CARD_TOO_SMALL,
            protection_cards=protection_regions,
        )
    unresolved_ambiguous = [
        candidate
        for candidate in unresolved
        if ambiguity_floor <= candidate.confidence < threshold
    ]
    if unresolved_ambiguous or len(trusted) > int(options.max_card_count):
        return _CardDetectionResult(
            cards=() if len(trusted) > int(options.max_card_count) else trusted_regions,
            candidate_count=candidate_count,
            reason_code=ReasonCode.CARD_DETECTION_UNCERTAIN,
            protection_cards=protection_regions,
        )
    if not trusted:
        return _CardDetectionResult(
            cards=(),
            candidate_count=candidate_count,
            reason_code=ReasonCode.CARD_NOT_FOUND,
            protection_cards=protection_regions,
        )
    return _CardDetectionResult(
        cards=trusted_regions,
        candidate_count=candidate_count,
        reason_code=None,
        protection_cards=protection_regions,
    )
