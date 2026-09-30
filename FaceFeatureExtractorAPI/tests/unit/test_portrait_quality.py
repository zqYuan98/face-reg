"""Portrait-only warnings must not weaken the shared strict face pipeline."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.core.pipeline import FaceQualityAnalysis
from face_algorithm.schemas import (
    IMAGE_RECAPTURE_REASON_CODES,
    VIDEO_RECAPTURE_REASON_CODES,
    FaceBox,
    Pose,
    ReasonCode,
    Suggestion,
)
from tests.unit.core.test_pipeline import (
    GOOD_BOX,
    GOOD_LANDMARKS,
    FaceAwareLandmarker,
    FakeAligner,
    FakeDetector,
    FakeEmbedder,
    FakeLandmarker,
    FakePoseEstimator,
    _pipeline,
    _profile,
    _textured_image,
)


def _portrait_profile(*, mode="exact_one"):
    return _profile(
        brightness=SimpleNamespace(v0=0.0, v1=32.0, v2=224.0, v3=255.0),
        resolution=SimpleNamespace(width=112, height=112),
        face_selection=SimpleNamespace(
            mode=mode,
            max_face_candidates=5,
            ignore_secondary_area_ratio=0.10,
            ambiguous_secondary_area_ratio=0.20,
            secondary_min_confidence=0.70,
        ),
    )


def _reason_values(result):
    return tuple(reason.value for reason in result.public.reason_codes)


def test_portrait_reason_codes_do_not_change_other_capability_reason_sets() -> None:
    for name in ("PORTRAIT_SOURCE_LIMITED", "PORTRAIT_UPSCALE_LIMITED"):
        assert name in ReasonCode.__members__
        assert ReasonCode[name] not in IMAGE_RECAPTURE_REASON_CODES
        assert ReasonCode[name] not in VIDEO_RECAPTURE_REASON_CODES


def test_portrait_quality_pass_retains_analysis_without_embedding() -> None:
    image = _textured_image()
    embedder = FakeEmbedder()
    pipeline = _pipeline(embedder=embedder)

    result = pipeline.analyze_portrait_quality(image, _portrait_profile())

    assert isinstance(result, FaceQualityAnalysis)
    assert result.public.success is True
    assert result.public.suggestion is Suggestion.PASS
    assert result.public.reason_codes == ()
    assert result.public.box == GOOD_BOX
    assert result.public.landmarks is not None
    assert result.public.pose == Pose(yaw=0, pitch=0, roll=0)
    assert result.public.quality is not None and result.public.quality.passed
    np.testing.assert_array_equal(result.corrected_image, image)
    assert result.corrected_image is not image
    assert result.aligned_face.shape == (256, 256, 3)
    assert result.public.processing_steps == (
        "rotation", "detection", "landmarks", "alignment", "quality"
    )
    assert not hasattr(result, "embedding")
    assert "embedding" not in result.public.model_dump_json()
    assert embedder.calls == 0


_SOFT_CASES = (
    ("dark", (0, 0, 0), ("TOO_DARK",)),
    ("bright", (0, 0, 0), ("TOO_BRIGHT",)),
    ("blur", (0, 0, 0), ("TOO_BLURRY",)),
    ("normal", (31, 0, 0), ("BAD_POSE",)),
    ("normal", (0, 26, 0), ("BAD_POSE",)),
    ("normal", (0, 0, 21), ("BAD_POSE",)),
    ("dark_blur", (0, 0, 0), ("TOO_DARK", "TOO_BLURRY")),
    ("bright_blur", (0, 0, 0), ("TOO_BRIGHT", "TOO_BLURRY")),
    ("dark", (31, 0, 0), ("TOO_DARK", "BAD_POSE")),
    ("bright", (31, 0, 0), ("TOO_BRIGHT", "BAD_POSE")),
    ("blur", (31, 0, 0), ("TOO_BLURRY", "BAD_POSE")),
    ("dark_blur", (31, 0, 0), ("TOO_DARK", "TOO_BLURRY", "BAD_POSE")),
    ("bright_blur", (31, 0, 0), ("TOO_BRIGHT", "TOO_BLURRY", "BAD_POSE")),
)


def _quality_image(kind):
    image = _textured_image()
    if kind == "dark":
        return image // 16
    if kind == "bright":
        return 224 + image // 8
    if kind == "blur":
        return np.full_like(image, 128)
    if kind == "dark_blur":
        return np.zeros_like(image)
    if kind == "bright_blur":
        return np.full_like(image, 255)
    return image


@pytest.mark.parametrize("kind,angles,expected", _SOFT_CASES)
@pytest.mark.parametrize("source_kind", ["normal", "margin", "overflow"])
def test_each_quality_warning_and_combination_completes_with_review(
    kind, angles, expected, source_kind
) -> None:
    box = {
        "normal": GOOD_BOX,
        "margin": FaceBox(x1=1, y1=100, x2=201, y2=300),
        "overflow": FaceBox(x1=-1, y1=100, x2=199, y2=300),
    }[source_kind]
    image = _quality_image(kind)
    embedder = FakeEmbedder()
    pipeline = _pipeline(
        detector=FakeDetector([box]),
        landmarker=FaceAwareLandmarker(),
        pose=FakePoseEstimator(Pose(yaw=angles[0], pitch=angles[1], roll=angles[2])),
        embedder=embedder,
    )

    result = pipeline.analyze_portrait_quality(image, _portrait_profile())

    expected_reasons = (("PORTRAIT_SOURCE_LIMITED",) if source_kind != "normal" else ()) + expected
    assert _reason_values(result) == expected_reasons
    assert result.public.success is True
    assert result.public.suggestion is Suggestion.REVIEW
    assert result.public.box == box.model_copy(update={"x1": max(0, box.x1)})
    assert result.public.landmarks is not None
    assert result.public.pose is not None
    assert result.public.quality is not None
    assert tuple(reason.value for reason in result.public.quality.reason_codes) == expected
    assert not result.public.quality.passed
    np.testing.assert_array_equal(result.corrected_image, image)
    assert result.aligned_face.shape == (256, 256, 3)
    assert "feature_extraction" not in result.public.processing_steps
    assert embedder.calls == 0


@pytest.mark.parametrize("kind,angles,expected", _SOFT_CASES)
def test_portrait_warning_policy_does_not_leak_into_existing_paths(kind, angles, expected):
    image = _quality_image(kind)
    profile = _portrait_profile()
    embedder = FakeEmbedder()
    pipeline = _pipeline(
        pose=FakePoseEstimator(Pose(yaw=angles[0], pitch=angles[1], roll=angles[2])),
        embedder=embedder,
    )

    portrait = pipeline.analyze_portrait_quality(image, profile)
    quality = pipeline.analyze_quality(image, profile)
    trusted = pipeline.analyze_trusted(image, profile)

    assert portrait.public.suggestion is Suggestion.REVIEW
    for strict in (quality, trusted):
        assert strict.public.success is True
        assert strict.public.suggestion is Suggestion.RECAPTURE
        assert _reason_values(strict) == expected
    assert trusted.embedding is None and embedder.calls == 0
    assert profile.brightness.v1 == 32 and profile.brightness.v2 == 224
    assert profile.resolution.width == profile.resolution.height == 112


@pytest.mark.parametrize("mode", ["exact_one", "dominant_primary"])
@pytest.mark.parametrize(
    "coordinates",
    [(0, 100, 200, 300), (1, 100, 201, 300), (200, 100, 400, 300),
     (199, 100, 399, 300), (100, 0, 300, 200), (100, 1, 300, 201),
     (100, 200, 300, 400), (100, 199, 300, 399)],
)
def test_in_bounds_edge_boxes_warn_only_after_valid_landmark_selection(mode, coordinates):
    box = FaceBox(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))
    landmarker = FaceAwareLandmarker()
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=landmarker)
    profile = _portrait_profile(mode=mode)

    result = pipeline.analyze_portrait_quality(_textured_image(), profile)

    assert _reason_values(result) == ("PORTRAIT_SOURCE_LIMITED",)
    assert result.public.success and result.public.suggestion is Suggestion.REVIEW
    assert result.public.quality.passed and result.public.landmarks is not None
    assert result.aligned_face is not None and landmarker.calls == 1
    for strict in (pipeline.analyze_quality, pipeline.analyze_trusted):
        assert strict(_textured_image(), profile).public.reason_codes == (ReasonCode.FACE_CROPPED,)


def test_valid_landmarks_alone_touching_margin_warn_without_reclassifying_bad_geometry():
    box = FaceBox(x1=9, y1=100, x2=200, y2=300)
    points = np.asarray([(7, 145), (170, 145), (100, 205), (40, 250), (150, 250)])
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FakeLandmarker(points))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert _reason_values(result) == ("PORTRAIT_SOURCE_LIMITED",)
    assert result.public.suggestion is Suggestion.REVIEW
    assert result.public.landmarks.points[0].x == 7
    assert result.aligned_face is not None


@pytest.mark.parametrize("mode", ["exact_one", "dominant_primary"])
@pytest.mark.parametrize(
    "coordinates",
    [(-11, 100, 189, 300), (211, 100, 411, 300),
     (100, -11, 300, 189), (100, 211, 300, 411)],
)
def test_severe_raw_box_overflow_remains_hard_cropped(mode, coordinates):
    box = FaceBox(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile(mode=mode))

    assert result.public.reason_codes == (ReasonCode.FACE_CROPPED,)
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.aligned_face is None and pipeline.aligner.calls == 0


@pytest.mark.parametrize("mode", ["exact_one", "dominant_primary"])
@pytest.mark.parametrize(
    "coordinates",
    [(-0.01, 100, 200, 300), (200, 100, 400.01, 300),
     (100, -0.01, 300, 200), (100, 200, 300, 400.01),
     (-10, 100, 190, 300), (210, 100, 410, 300),
     (100, -10, 300, 190), (100, 210, 300, 410),
     (-10, -10, 190, 190)],
)
def test_minor_raw_box_overflow_warns_only_in_portrait(mode, coordinates):
    box = FaceBox(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())
    profile = _portrait_profile(mode=mode)
    image = _textured_image()

    result = pipeline.analyze_portrait_quality(image, profile)

    assert result.public.reason_codes == (ReasonCode.PORTRAIT_SOURCE_LIMITED,)
    assert result.public.success and result.public.suggestion is Suggestion.REVIEW
    assert result.public.box == FaceBox(
        x1=max(0, box.x1), y1=max(0, box.y1),
        x2=min(400, box.x2), y2=min(400, box.y2),
    )
    assert result.public.quality.passed
    resolution = next(metric for metric in result.public.quality.metrics if metric.name == "resolution")
    assert resolution.value == pytest.approx(
        min(result.public.box.width, result.public.box.height) / 112
    )
    assert result.aligned_face is not None
    assert "feature_extraction" not in result.public.processing_steps
    assert pipeline.embedder.calls == 0
    for strict in (pipeline.analyze_quality, pipeline.analyze_trusted):
        strict_result = strict(image, profile)
        assert strict_result.public.reason_codes == (ReasonCode.FACE_CROPPED,)
        assert strict_result.public.suggestion is Suggestion.RECAPTURE
        assert strict_result.aligned_face is None
    assert pipeline.embedder.calls == 0


def test_reported_717_by_720_source_uses_visible_box_with_source_warning():
    image = np.tile(_textured_image(), (2, 2, 1))[:720, :717].copy()
    box = FaceBox(x1=58.450283, y1=31.375793, x2=610.558960, y2=737.061401)
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())

    result = pipeline.analyze_portrait_quality(image, _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.PORTRAIT_SOURCE_LIMITED,)
    assert result.public.success and result.public.suggestion is Suggestion.REVIEW
    assert result.public.box == FaceBox(
        x1=box.x1, y1=box.y1, x2=box.x2, y2=720,
    )
    assert result.public.quality.passed and result.aligned_face is not None
    assert all(0 <= point.x < 717 and 0 <= point.y < 720
               for point in result.public.landmarks.points)
    np.testing.assert_array_equal(result.corrected_image, image)
    assert box.y2 == 737.061401
    assert pipeline.embedder.calls == 0


@pytest.mark.parametrize("height,accepted", [(200, True), (199, False)])
def test_visible_area_ninety_percent_is_inclusive_but_combined_overflow_can_fail(height, accepted):
    image = _textured_image()[:height, :180].copy()
    box = FaceBox(x1=-10, y1=0, x2=190, y2=200)
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())

    result = pipeline.analyze_portrait_quality(image, _portrait_profile())

    assert result.public.reason_codes == (
        ReasonCode.PORTRAIT_SOURCE_LIMITED if accepted else ReasonCode.FACE_CROPPED,
    )
    assert result.public.suggestion is (Suggestion.REVIEW if accepted else Suggestion.RECAPTURE)
    assert (result.aligned_face is not None) is accepted
    assert pipeline.embedder.calls == 0


@pytest.mark.parametrize(
    "coordinates",
    [(-1, 100, 111.9, 300), (288.1, 100, 401, 300),
     (100, -1, 300, 111.9), (100, 288.1, 300, 401)],
)
def test_minor_overflow_cannot_count_invisible_pixels_towards_minimum(coordinates):
    box = FaceBox(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.FACE_TOO_SMALL,)
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.aligned_face is None and pipeline.aligner.calls == 0
    assert pipeline.embedder.calls == 0


@pytest.mark.parametrize(
    "coordinates",
    [(-1, 100, 112, 300), (288, 100, 401, 300),
     (100, -1, 300, 112), (100, 288, 300, 401)],
)
def test_minor_overflow_at_visible_profile_minimum_is_allowed(coordinates):
    box = FaceBox(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.PORTRAIT_SOURCE_LIMITED,)
    assert result.public.suggestion is Suggestion.REVIEW
    assert result.public.quality.passed and result.aligned_face is not None
    assert pipeline.embedder.calls == 0


@pytest.mark.parametrize("coordinate", [-0.01, 400.0, 400.01])
@pytest.mark.parametrize("overflow", [False, True])
@pytest.mark.parametrize("axis", [0, 1])
def test_actual_landmark_overflow_is_not_a_source_margin_warning(coordinate, overflow, axis):
    start = -0.01 if overflow else 0
    box = (FaceBox(x1=start, y1=100, x2=400, y2=300) if axis == 0
           else FaceBox(x1=100, y1=start, x2=300, y2=400))
    points = GOOD_LANDMARKS.copy()
    if axis == 0:
        points[0 if coordinate < 0 else 1, 0] = coordinate
    else:
        points[[0, 1] if coordinate < 0 else [3, 4], 1] = coordinate
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FakeLandmarker(points))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.FACE_CROPPED,)
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.aligned_face is None


@pytest.mark.parametrize(
    "points,expected",
    [(GOOD_LANDMARKS[[1, 0, 2, 3, 4]], ReasonCode.LANDMARK_FAILED),
     (np.full((5, 2), np.nan), ReasonCode.INFERENCE_ERROR),
     (np.full((5, 2), np.inf), ReasonCode.INFERENCE_ERROR),
     (np.zeros((4, 2)), ReasonCode.INFERENCE_ERROR)],
)
@pytest.mark.parametrize("overflow", [False, True])
def test_invalid_landmarks_cannot_be_softened(points, expected, overflow):
    box = FaceBox(x1=-0.01, y1=100, x2=400, y2=300) if overflow else GOOD_BOX
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FakeLandmarker(points))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (expected,)
    assert result.aligned_face is None and pipeline.aligner.calls == 0
    assert result.public.success is (expected is not ReasonCode.INFERENCE_ERROR)


def test_border_touch_does_not_soften_invalid_landmark_geometry():
    box = FaceBox(x1=0, y1=100, x2=200, y2=300)
    points = np.asarray([(2, 160), (170, 160), (100, 300), (40, 250), (150, 250)])
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FakeLandmarker(points))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.FACE_CROPPED,)
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.aligned_face is None and pipeline.aligner.calls == 0


@pytest.mark.parametrize("width,height", [(111.9, 112), (112, 111.9), (111, 111)])
def test_source_face_below_profile_minimum_still_fails(width, height):
    box = FaceBox(x1=100, y1=100, x2=100 + width, y2=100 + height)
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.FACE_TOO_SMALL,)
    assert result.aligned_face is None and pipeline.aligner.calls == 0


@pytest.mark.parametrize("size", [112, 150, 204])
def test_profile_sized_faces_do_not_gain_a_template_derived_minimum(size):
    box = FaceBox(x1=100, y1=100, x2=100 + size, y2=100 + size)
    pipeline = _pipeline(detector=FakeDetector([box]), landmarker=FaceAwareLandmarker())

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.suggestion is Suggestion.PASS
    assert result.public.reason_codes == ()
    assert result.aligned_face is not None


@pytest.mark.parametrize("mode", ["exact_one", "dominant_primary"])
@pytest.mark.parametrize("overflow", [False, True])
def test_ambiguous_targets_remain_hard_failure(mode, overflow):
    primary = FaceBox(x1=-1, y1=100, x2=210, y2=300) if overflow else GOOD_BOX
    second = FaceBox(x1=20, y1=100, x2=210, y2=290)
    pipeline = _pipeline(
        detector=FakeDetector([primary, second]), landmarker=FaceAwareLandmarker()
    )

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile(mode=mode))

    assert result.public.reason_codes == (ReasonCode.MULTIPLE_FACES,)
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.public.box is None and result.aligned_face is None


def test_no_face_remains_hard_failure():
    pipeline = _pipeline(detector=FakeDetector([]))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.NO_FACE,)
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.aligned_face is None


@pytest.mark.parametrize("mode", ["exact_one", "dominant_primary"])
def test_fully_outside_face_remains_blocked_before_landmark_inference(mode):
    pipeline = _pipeline(detector=FakeDetector([FaceBox(x1=401, y1=100, x2=601, y2=300)]))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile(mode=mode))

    expected = ReasonCode.FACE_CROPPED if mode == "exact_one" else ReasonCode.NO_FACE
    assert result.public.reason_codes == (expected,)
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.aligned_face is None
    assert pipeline.landmarker.calls == pipeline.aligner.calls == pipeline.embedder.calls == 0


@pytest.mark.parametrize("coordinates", [(np.nan, 100, 300, 300), (100, 100, np.inf, 300), (300, 100, 100, 300)])
def test_invalid_detector_boxes_remain_inference_failures(coordinates):
    box = SimpleNamespace(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))
    pipeline = _pipeline(detector=FakeDetector([box]))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert result.public.success is False
    assert result.aligned_face is None


@pytest.mark.parametrize(
    "component,method",
    [("rotator", "correct"), ("detector", "detect"), ("landmarker", "detect"),
     ("aligner", "align"), ("pose_estimator", "estimate")],
)
@pytest.mark.parametrize("overflow", [False, True])
def test_model_exceptions_remain_incomplete_reviews(component, method, monkeypatch, overflow):
    box = FaceBox(x1=-0.01, y1=100, x2=400, y2=300) if overflow else GOOD_BOX
    pipeline = _pipeline(detector=FakeDetector([box]))

    def fail(*args):
        raise RuntimeError("private model diagnostics must not escape")

    monkeypatch.setattr(getattr(pipeline, component), method, fail)
    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.success is False
    assert result.public.suggestion is Suggestion.REVIEW
    assert result.public.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert "private" not in result.public.model_dump_json()
    assert pipeline.embedder.calls == 0


def test_invalid_alignment_remains_a_hard_failure():
    pipeline = _pipeline(aligner=FakeAligner(aligned=np.zeros((128, 128, 3), dtype=np.uint8)))

    result = pipeline.analyze_portrait_quality(_textured_image(), _portrait_profile())

    assert result.public.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert result.public.success is False
    assert pipeline.pose_estimator.calls == pipeline.embedder.calls == 0
