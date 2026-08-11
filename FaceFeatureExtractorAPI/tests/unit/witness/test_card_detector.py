from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from face_algorithm.schemas import FaceBox, ReasonCode
from face_algorithm.witness.card_detector import _Candidate, _detect_card_regions, _nms


FACE = FaceBox(x1=170.0, y1=90.0, x2=310.0, y2=250.0, confidence=0.99)


def _options(**updates):
    values = {
        "min_card_area_ratio": 0.008,
        "max_card_area_ratio": 0.12,
        "min_card_aspect_ratio": 1.20,
        "max_card_aspect_ratio": 2.20,
        "max_card_count": 3,
        "min_card_width_to_face_ratio": 0.55,
        "max_card_width_to_face_ratio": 1.60,
        "min_card_area_to_face_ratio": 0.10,
        "max_card_area_to_face_ratio": 0.90,
        "min_card_rectangularity": 0.72,
        "max_card_angle_deviation_degrees": 25.0,
        "min_card_candidate_confidence": 0.62,
        "card_ambiguity_margin": 0.08,
        "card_contact_dilation_ratio": 0.03,
        "card_margin_ratio": 0.02,
        "card_nms_iou_threshold": 0.35,
        "card_edge_margin_ratio": 0.005,
        "subject_edge_alpha": 0.15,
        "card_detection_max_dimension": 640,
        "min_card_short_side_pixels": 60,
        "max_card_center_offset_to_face_width_ratio": 1.50,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _scene(cards=(), *, subject_card_holes=()) -> tuple[np.ndarray, np.ndarray]:
    height, width = 640, 480
    y, x = np.indices((height, width))
    image = np.stack(
        (
            (40 + (x % 29) * 2).astype(np.uint8),
            (55 + (y % 31) * 2).astype(np.uint8),
            (65 + ((x + y) % 23) * 2).astype(np.uint8),
        ),
        axis=2,
    )
    subject = np.zeros((height, width), dtype=np.float32)
    subject[65:630, 95:385] = 1.0
    for quad in cards:
        points = np.asarray(quad, dtype=np.int32)
        cv2.fillConvexPoly(image, points, (235, 235, 235))
        cv2.polylines(image, [points], True, (15, 15, 15), 4, cv2.LINE_AA)
    for quad in subject_card_holes:
        cv2.fillConvexPoly(subject, np.asarray(quad, dtype=np.int32), 0.0)
    return np.ascontiguousarray(image), subject


CARD_1 = ((150, 340), (320, 340), (320, 445), (150, 445))
CARD_2 = ((155, 475), (305, 462), (315, 552), (165, 565))


def test_detects_one_trusted_card_below_the_face() -> None:
    image, subject = _scene((CARD_1,))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.reason_code is None
    assert result.candidate_count >= 1
    assert len(result.cards) == 1
    assert result.cards[0].confidence >= 0.62
    assert len(result.cards[0].corners) == 4


def test_detects_and_preserves_two_distinct_trusted_cards() -> None:
    image, subject = _scene((CARD_1, CARD_2))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.reason_code is None
    assert len(result.cards) == 2
    centers_y = sorted(
        sum(point.y for point in card.corners) / 4.0 for card in result.cards
    )
    assert centers_y[1] - centers_y[0] > 80


def test_nested_card_contours_are_deduplicated_by_quad_nms() -> None:
    image, subject = _scene((CARD_1,))
    cv2.polylines(
        image,
        [np.asarray(((155, 345), (315, 345), (315, 440), (155, 440)))],
        True,
        (60, 60, 60),
        2,
    )

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.reason_code is None
    assert len(result.cards) == 1


def test_nms_keeps_partially_overlapping_near_score_candidate_for_safety() -> None:
    first_quad = np.asarray(
        ((100, 100), (200, 100), (200, 160), (100, 160)), np.float32
    )
    second_quad = np.asarray(
        ((136, 100), (236, 100), (236, 160), (136, 160)), np.float32
    )
    candidates = [
        _Candidate(first_quad, 6_000.0, 0.90, False, False),
        _Candidate(second_quad, 6_000.0, 0.65, False, False),
    ]

    kept = _nms(candidates, threshold=0.35, protection_margin_ratio=0.02)

    assert len(kept) == 2


def test_nms_never_suppresses_a_candidate_with_unprotected_edge_pixels() -> None:
    protector_quad = np.asarray(
        ((100, 100), (200, 100), (200, 160), (100, 160)), np.float32
    )
    near_quad = np.asarray(
        ((100, 100), (204, 100), (204, 160), (100, 160)), np.float32
    )
    candidates = [
        _Candidate(protector_quad, 6_000.0, 0.90, False, False),
        _Candidate(near_quad, 6_240.0, 0.65, False, False),
    ]

    kept = _nms(candidates, threshold=0.35, protection_margin_ratio=0.02)

    assert len(kept) == 2


def test_large_background_rectangle_is_not_accepted_as_a_handheld_card() -> None:
    image, subject = _scene(())
    cv2.rectangle(image, (10, 280), (465, 610), (230, 230, 230), 4)

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_NOT_FOUND


def test_missing_card_cannot_be_configured_to_pass() -> None:
    image, subject = _scene(())

    result = _detect_card_regions(
        image,
        FACE,
        subject,
        _options(require_card=False),
    )

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_NOT_FOUND


def test_paper_sized_rectangle_is_rejected_by_face_relative_scale() -> None:
    paper = ((80, 280), (405, 280), (405, 590), (80, 590))
    image, subject = _scene((paper,))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_NOT_FOUND


def test_mild_perspective_card_is_accepted() -> None:
    perspective = ((145, 350), (325, 330), (310, 445), (160, 460))
    image, subject = _scene((perspective,))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.reason_code is None
    assert len(result.cards) == 1


def test_hand_occlusion_does_not_drop_a_still_rectangular_card() -> None:
    image, subject = _scene((CARD_1,))
    cv2.circle(image, (158, 430), 28, (55, 75, 95), -1)
    cv2.circle(image, (312, 430), 28, (55, 75, 95), -1)

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.reason_code is None
    assert len(result.cards) == 1


def test_card_visible_inside_a_phone_screen_is_protected() -> None:
    image, subject = _scene(())
    cv2.rectangle(image, (115, 270), (365, 585), (20, 20, 20), -1)
    screen_card = np.asarray(((150, 365), (330, 355), (335, 465), (155, 475)))
    cv2.fillConvexPoly(image, screen_card, (230, 230, 230))
    cv2.polylines(image, [screen_card], True, (40, 40, 40), 3, cv2.LINE_AA)

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.reason_code is None
    assert len(result.cards) >= 1
    assert any(
        220.0 <= sum(point.x for point in card.corners) / 4.0 <= 270.0
        for card in result.cards
    )


def test_card_can_bridge_a_hole_in_the_subject_matte_when_it_is_near_the_person() -> None:
    image, subject = _scene((CARD_1,), subject_card_holes=(CARD_1,))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.reason_code is None
    assert len(result.cards) == 1


@pytest.mark.parametrize(
    "edge_card",
    [
        ((0, 350), (150, 350), (150, 440), (0, 440)),
        ((330, 350), (479, 350), (479, 440), (330, 440)),
        ((165, 550), (315, 550), (315, 639), (165, 639)),
    ],
)
def test_card_touching_the_image_edge_requires_recapture(edge_card) -> None:
    image, subject = _scene((edge_card,))
    cv2.fillConvexPoly(subject, np.asarray(edge_card, np.int32), 1.0)

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_CROPPED


def test_only_plausible_but_too_small_card_reports_too_small() -> None:
    tiny_card = ((205, 340), (275, 340), (275, 382), (205, 382))
    image, subject = _scene((tiny_card,))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_TOO_SMALL


def test_trusted_card_plus_a_plausible_small_second_card_does_not_pass() -> None:
    small_second = ((180, 500), (250, 500), (250, 542), (180, 542))
    image, subject = _scene((CARD_1, small_second))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_TOO_SMALL


def test_plausible_small_card_touching_the_edge_reports_cropped_first() -> None:
    small_edge = ((0, 350), (70, 350), (70, 392), (0, 392))
    image, subject = _scene((small_edge,))
    cv2.fillConvexPoly(subject, np.asarray(small_edge, np.int32), 1.0)

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_CROPPED


def test_logo_sized_rectangle_is_not_misreported_as_a_small_whole_card() -> None:
    logo = ((210, 340), (250, 340), (250, 364), (210, 364))
    image, subject = _scene((logo,))

    result = _detect_card_regions(image, FACE, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_NOT_FOUND


def test_face_relative_logo_fragment_is_not_a_small_card_candidate() -> None:
    logo = ((205, 340), (275, 340), (275, 382), (205, 382))
    image, subject = _scene((logo,))
    large_face = FaceBox(
        x1=90.0,
        y1=20.0,
        x2=390.0,
        y2=300.0,
        confidence=0.99,
    )

    result = _detect_card_regions(image, large_face, subject, _options())

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_NOT_FOUND


def test_card_proposals_are_generated_at_bounded_working_resolution(monkeypatch) -> None:
    image, subject = _scene((CARD_1,))
    image = cv2.resize(image, (1920, 2560), interpolation=cv2.INTER_NEAREST)
    subject = cv2.resize(subject, (1920, 2560), interpolation=cv2.INTER_NEAREST)
    face = FaceBox(
        x1=FACE.x1 * 4,
        y1=FACE.y1 * 4,
        x2=FACE.x2 * 4,
        y2=FACE.y2 * 4,
        confidence=FACE.confidence,
    )
    seen_shapes: list[tuple[int, int]] = []
    original_canny = cv2.Canny

    def capture(image, *args, **kwargs):
        seen_shapes.append(image.shape[:2])
        return original_canny(image, *args, **kwargs)

    monkeypatch.setattr(cv2, "Canny", capture)

    result = _detect_card_regions(image, face, subject, _options())

    assert result.reason_code is None
    assert len(result.cards) == 1
    assert seen_shapes and max(seen_shapes[0]) <= 640
    center_x = sum(point.x for point in result.cards[0].corners) / 4.0
    center_y = sum(point.y for point in result.cards[0].corners) / 4.0
    assert center_x == pytest.approx(235.0 * 4, abs=12.0)
    assert center_y == pytest.approx(392.5 * 4, abs=12.0)


def test_near_threshold_candidate_is_uncertain_instead_of_silently_missing() -> None:
    perspective = ((145, 350), (325, 330), (310, 445), (160, 460))
    image, subject = _scene((perspective,))

    result = _detect_card_regions(
        image,
        FACE,
        subject,
        _options(min_card_candidate_confidence=1.0, card_ambiguity_margin=0.50),
    )

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_DETECTION_UNCERTAIN


def test_more_than_configured_trusted_cards_is_uncertain_not_truncated() -> None:
    cards = (
        ((120, 290), (250, 290), (250, 370), (120, 370)),
        ((250, 385), (380, 385), (380, 465), (250, 465)),
    )
    image, subject = _scene(cards)

    result = _detect_card_regions(image, FACE, subject, _options(max_card_count=1))

    assert result.cards == ()
    assert result.reason_code is ReasonCode.CARD_DETECTION_UNCERTAIN


def test_candidate_exactly_on_inclusive_size_boundaries_is_allowed() -> None:
    image, subject = _scene((CARD_1,))
    base = _detect_card_regions(image, FACE, subject, _options())
    assert len(base.cards) == 1

    card = base.cards[0]
    points = np.asarray([(point.x, point.y) for point in card.corners], np.float32)
    area = abs(float(cv2.contourArea(points)))
    rect = cv2.minAreaRect(points)
    width = max(rect[1])
    image_area = float(image.shape[0] * image.shape[1])

    exact = _detect_card_regions(
        image,
        FACE,
        subject,
        _options(
            min_card_area_ratio=area / image_area,
            max_card_area_ratio=area / image_area,
            min_card_width_to_face_ratio=width / FACE.width,
            max_card_width_to_face_ratio=width / FACE.width,
            min_card_area_to_face_ratio=area / (FACE.width * FACE.height),
            max_card_area_to_face_ratio=area / (FACE.width * FACE.height),
        ),
    )

    assert exact.reason_code is None
    assert len(exact.cards) == 1


def test_detector_does_not_mutate_image_or_subject_mask() -> None:
    image, subject = _scene((CARD_1,))
    original_image = image.copy()
    original_subject = subject.copy()

    _detect_card_regions(image, FACE, subject, _options())

    np.testing.assert_array_equal(image, original_image)
    np.testing.assert_array_equal(subject, original_subject)
