from __future__ import annotations

from itertools import permutations
from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.core.face_selection import FaceCandidateBatch, FaceCandidateSelector
from face_algorithm.schemas import FaceBox, ReasonCode


IMAGE = np.zeros((500, 800, 3), dtype=np.uint8)
GEOMETRY = SimpleNamespace(
    require_points_inside_bbox=True,
    bbox_tolerance_ratio=0.02,
    min_eye_distance_ratio=0.15,
    min_mouth_width_ratio=0.10,
    max_eye_vertical_difference_ratio=0.20,
)
EXACT = SimpleNamespace(mode="exact_one")
DOMINANT = SimpleNamespace(
    mode="dominant_primary",
    max_face_candidates=5,
    ignore_secondary_area_ratio=0.10,
    ambiguous_secondary_area_ratio=0.20,
    secondary_min_confidence=0.70,
    selector_version="dominant-primary-v1",
)


def _box(
    x: float,
    y: float,
    size: float,
    confidence: float = 0.90,
) -> FaceBox:
    return FaceBox(
        x1=x,
        y1=y,
        x2=x + size,
        y2=y + size,
        confidence=confidence,
    )


def _valid_landmarks(face: FaceBox) -> np.ndarray:
    return np.asarray(
        [
            (face.x1 + 0.25 * face.width, face.y1 + 0.30 * face.height),
            (face.x1 + 0.75 * face.width, face.y1 + 0.30 * face.height),
            (face.x1 + 0.50 * face.width, face.y1 + 0.52 * face.height),
            (face.x1 + 0.32 * face.width, face.y1 + 0.75 * face.height),
            (face.x1 + 0.68 * face.width, face.y1 + 0.75 * face.height),
        ],
        dtype=np.float32,
    )


class GeometryLandmarker:
    def __init__(self, invalid_x1: set[float] | None = None) -> None:
        self.invalid_x1 = invalid_x1 or set()
        self.calls: list[FaceBox] = []

    def detect(self, _image: np.ndarray, face: FaceBox) -> np.ndarray:
        self.calls.append(face)
        points = _valid_landmarks(face)
        if face.x1 in self.invalid_x1:
            points[[0, 1]] = points[[1, 0]]
        return points


def test_exact_one_keeps_strict_zero_one_many_semantics() -> None:
    selector = FaceCandidateSelector()
    landmarker = GeometryLandmarker()
    one = _box(100, 100, 200)

    no_face = selector.select(IMAGE, [], landmarker, EXACT, GEOMETRY)
    selected = selector.select(IMAGE, [one], landmarker, EXACT, GEOMETRY)
    multiple = selector.select(
        IMAGE,
        [one, _box(450, 100, 120)],
        landmarker,
        EXACT,
        GEOMETRY,
    )

    assert no_face.reason_code is ReasonCode.NO_FACE
    assert selected.reason_code is None
    assert selected.box == one
    assert selected.landmarks is not None
    assert multiple.reason_code is ReasonCode.MULTIPLE_FACES
    assert len(landmarker.calls) == 1


def test_dominant_selection_is_invariant_to_candidate_input_order() -> None:
    selector = FaceCandidateSelector()
    primary = _box(250, 100, 200, 0.80)
    tiny = _box(40, 40, 50, 0.99)
    invalid = _box(600, 280, 80, 0.40)
    selected_coordinates = set()

    for candidate_order in permutations((tiny, primary, invalid)):
        result = selector.select(
            IMAGE,
            candidate_order,
            GeometryLandmarker({invalid.x1}),
            DOMINANT,
            GEOMETRY,
        )
        assert result.reason_code is None
        assert result.box is not None
        selected_coordinates.add((result.box.x1, result.box.y1))

    assert selected_coordinates == {(primary.x1, primary.y1)}


def test_stable_sort_uses_confidence_center_distance_then_coordinates() -> None:
    selector = FaceCandidateSelector()
    high_confidence_edge = _box(20, 180, 100, 0.95)
    lower_confidence_center = _box(350, 200, 100, 0.90)
    prepared = selector.prepare(
        IMAGE,
        [lower_confidence_center, high_confidence_edge],
        GeometryLandmarker(),
        DOMINANT,
        GEOMETRY,
    )
    assert prepared.box == high_confidence_edge

    left = _box(100, 200, 100, 0.90)
    right = _box(600, 200, 100, 0.90)
    for order in ((right, left), (left, right)):
        tied = selector.prepare(
            IMAGE,
            order,
            GeometryLandmarker(),
            DOMINANT,
            GEOMETRY,
        )
        assert tied.box == left


def test_sixth_saturation_candidate_fails_closed_before_landmarks() -> None:
    selector = FaceCandidateSelector()
    landmarker = GeometryLandmarker()
    candidates = [_box(20 + index * 90, 100, 70) for index in range(6)]

    result = selector.select(IMAGE, candidates, landmarker, DOMINANT, GEOMETRY)

    assert result.reason_code is ReasonCode.MULTIPLE_FACES
    assert result.candidate_count == 6
    assert result.saturated is True
    assert landmarker.calls == []


def test_five_fully_outside_boxes_do_not_create_false_saturation() -> None:
    selector = FaceCandidateSelector()
    primary = _box(250, 100, 200)
    outside = [
        FaceBox(
            x1=-300.0 - index * 50.0,
            y1=50.0,
            x2=-250.0 - index * 50.0,
            y2=100.0,
            confidence=0.99,
        )
        for index in range(5)
    ]
    landmarker = GeometryLandmarker()

    result = selector.select(
        IMAGE,
        [primary, *outside],
        landmarker,
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is None
    assert result.candidate_count == 1
    assert result.saturated is False
    assert len(landmarker.calls) == 1


@pytest.mark.parametrize(
    "secondary",
    [
        FaceBox(
            x1=-300.0,
            y1=50.0,
            x2=-250.0,
            y2=100.0,
            confidence=0.99,
        ),
        SimpleNamespace(x1="malformed"),
    ],
)
def test_exact_one_uses_raw_count_before_normalization(
    secondary: object,
) -> None:
    primary = _box(250, 100, 200)
    landmarker = GeometryLandmarker()

    result = FaceCandidateSelector().select(
        IMAGE,
        [primary, secondary],
        landmarker,
        EXACT,
        GEOMETRY,
    )

    assert result.reason_code is ReasonCode.MULTIPLE_FACES
    assert result.candidate_count == 2
    assert landmarker.calls == []


def test_exact_one_uses_production_batch_raw_count_before_normalization() -> None:
    primary = _box(250, 100, 200)
    candidates = FaceCandidateBatch(
        (primary,),
        raw_candidate_count=2,
        saturated=False,
    )
    landmarker = GeometryLandmarker()

    result = FaceCandidateSelector().select(
        IMAGE,
        candidates,
        landmarker,
        EXACT,
        GEOMETRY,
    )

    assert result.reason_code is ReasonCode.MULTIPLE_FACES
    assert result.candidate_count == 2
    assert landmarker.calls == []


@pytest.mark.parametrize("invalid_count", [True, "2", -1, 0])
def test_candidate_batch_strictly_validates_raw_candidate_count(
    invalid_count: object,
) -> None:
    primary = _box(250, 100, 200)

    with pytest.raises((TypeError, ValueError)):
        FaceCandidateBatch(
            (primary,),
            raw_candidate_count=invalid_count,
            saturated=False,
        )


def test_provider_saturation_marker_fails_closed_after_outside_filtering() -> None:
    primary = _box(250, 100, 200)
    candidates = FaceCandidateBatch(
        (primary,),
        raw_candidate_count=65,
        saturated=True,
    )
    landmarker = GeometryLandmarker()

    result = FaceCandidateSelector().select(
        IMAGE,
        candidates,
        landmarker,
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is ReasonCode.MULTIPLE_FACES
    assert result.saturated is True
    assert landmarker.calls == []


@pytest.mark.parametrize(
    ("ratio", "confidence", "valid", "expected"),
    [
        (0.099, 0.99, True, None),
        (0.100, 0.70, True, ReasonCode.MULTIPLE_FACES),
        (0.100, 0.69, True, None),
        (0.100, 0.99, False, None),
        (0.199, 0.70, True, ReasonCode.MULTIPLE_FACES),
        (0.200, 0.10, True, ReasonCode.MULTIPLE_FACES),
        (0.200, 0.70, False, ReasonCode.MULTIPLE_FACES),
        (0.200, 0.69, False, None),
    ],
)
def test_dominant_secondary_area_decision_table(
    ratio: float,
    confidence: float,
    valid: bool,
    expected: ReasonCode | None,
) -> None:
    selector = FaceCandidateSelector()
    primary = _box(300, 100, 200, 0.95)
    secondary_size = primary.width * ratio**0.5
    secondary = _box(50, 300, secondary_size, confidence)
    invalid = set() if valid else {secondary.x1}

    result = selector.select(
        IMAGE,
        [secondary, primary],
        GeometryLandmarker(invalid),
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is expected


def test_tiny_secondary_is_ignored_without_landmark_inference() -> None:
    primary = _box(250, 100, 200)
    tiny = _box(30, 30, 50, 0.99)
    landmarker = GeometryLandmarker()

    result = FaceCandidateSelector().select(
        IMAGE,
        [tiny, primary],
        landmarker,
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is None
    assert [face.x1 for face in landmarker.calls] == [primary.x1]


def test_low_confidence_large_invalid_box_is_ignored() -> None:
    primary = _box(300, 100, 180, 0.90)
    false_positive = _box(50, 100, 190, 0.69)
    landmarker = GeometryLandmarker({false_positive.x1})

    result = FaceCandidateSelector().select(
        IMAGE,
        [primary, false_positive],
        landmarker,
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is None
    assert result.box == primary


def test_no_geometrically_valid_primary_returns_landmark_failed() -> None:
    candidate = _box(250, 100, 200)

    result = FaceCandidateSelector().select(
        IMAGE,
        [candidate],
        GeometryLandmarker({candidate.x1}),
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is ReasonCode.LANDMARK_FAILED


class RaisingLandmarker:
    def detect(self, _image: np.ndarray, _face: FaceBox) -> np.ndarray:
        raise RuntimeError("synthetic inference failure")


def test_landmark_inference_exception_maps_to_inference_error() -> None:
    result = FaceCandidateSelector().select(
        IMAGE,
        [_box(250, 100, 200)],
        RaisingLandmarker(),
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is ReasonCode.INFERENCE_ERROR


class SecondaryRaisingLandmarker(GeometryLandmarker):
    def __init__(self, raising_x1: float) -> None:
        super().__init__()
        self.raising_x1 = raising_x1

    def detect(self, image: np.ndarray, face: FaceBox) -> np.ndarray:
        if face.x1 == self.raising_x1:
            raise RuntimeError("synthetic secondary inference failure")
        return super().detect(image, face)


def test_secondary_landmark_exception_maps_to_inference_error() -> None:
    primary = _box(300, 100, 200)
    secondary = _box(50, 250, 100, 0.99)

    result = FaceCandidateSelector().select(
        IMAGE,
        [primary, secondary],
        SecondaryRaisingLandmarker(secondary.x1),
        DOMINANT,
        GEOMETRY,
    )

    assert result.reason_code is ReasonCode.INFERENCE_ERROR


def test_secondary_with_at_least_eighty_percent_ignore_region_coverage_is_ignored() -> None:
    primary = _box(300, 100, 200)
    secondary = _box(50, 250, 100, 0.99)
    # Covers exactly 80% of the secondary face box.
    ignore_region = FaceBox(x1=50, y1=250, x2=130, y2=350)
    landmarker = GeometryLandmarker()

    result = FaceCandidateSelector().select(
        IMAGE,
        [primary, secondary],
        landmarker,
        DOMINANT,
        GEOMETRY,
        ignore_regions=(ignore_region,),
    )

    assert result.reason_code is None
    assert [face.x1 for face in landmarker.calls] == [primary.x1]


def test_secondary_below_eighty_percent_ignore_region_coverage_uses_area_rules() -> None:
    primary = _box(300, 100, 200)
    secondary = _box(50, 250, 100, 0.99)
    ignore_region = FaceBox(x1=50, y1=250, x2=129.9, y2=350)

    result = FaceCandidateSelector().select(
        IMAGE,
        [primary, secondary],
        GeometryLandmarker(),
        DOMINANT,
        GEOMETRY,
        ignore_regions=(ignore_region,),
    )

    assert result.reason_code is ReasonCode.MULTIPLE_FACES


def test_prepare_finalize_reuses_primary_landmarks() -> None:
    primary = _box(300, 100, 200)
    tiny = _box(50, 250, 50)
    landmarker = GeometryLandmarker()
    selector = FaceCandidateSelector()

    prepared = selector.prepare(
        IMAGE,
        [primary, tiny],
        landmarker,
        DOMINANT,
        GEOMETRY,
    )
    result = selector.finalize(
        IMAGE,
        landmarker,
        prepared,
        ignore_regions=(),
    )

    assert result.reason_code is None
    assert len(landmarker.calls) == 1
    assert np.array_equal(result.landmarks, _valid_landmarks(primary))
