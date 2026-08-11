from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import math
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.core.pipeline import FacePipeline
from face_algorithm.core.protocols import RotationResult
from face_algorithm.runtime.model_registry import LoadedModelHandle, VerifiedModelEvidence
from face_algorithm.schemas import FaceBox, Pose, ReasonCode, Suggestion


GOOD_BOX = FaceBox(
    x1=100.0,
    y1=100.0,
    x2=300.0,
    y2=300.0,
    confidence=0.99,
)
GOOD_LANDMARKS = np.asarray(
    [
        (150.0, 160.0),
        (250.0, 160.0),
        (200.0, 205.0),
        (165.0, 250.0),
        (235.0, 250.0),
    ],
    dtype=np.float32,
)


def _profile(**overrides):
    values = {
        "require_single_face": True,
        "brightness": SimpleNamespace(v0=0.0, v1=1.0, v2=254.0, v3=255.0),
        "resolution": SimpleNamespace(width=100, height=100),
        "pose": SimpleNamespace(
            yaw_threshold=30.0,
            pitch_threshold=25.0,
            roll_threshold=20.0,
        ),
        "clarity": SimpleNamespace(low_threshold=0.01, high_threshold=0.02),
        "border_margin_ratio": 0.02,
        "landmark_geometry": SimpleNamespace(
            require_points_inside_bbox=True,
            bbox_tolerance_ratio=0.02,
            min_eye_distance_ratio=0.15,
            min_mouth_width_ratio=0.10,
            max_eye_vertical_difference_ratio=0.20,
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _textured_image() -> np.ndarray:
    y, x = np.indices((400, 400))
    base = (96 + ((x * 17 + y * 29) % 64)).astype(np.uint8)
    return np.stack((base, np.roll(base, 3, axis=1), np.roll(base, 5, axis=0)), axis=2)


class FakeRotator:
    def __init__(self, quarter_turns: int = 0):
        self.quarter_turns = quarter_turns
        self.calls = 0

    def correct(self, image: np.ndarray) -> RotationResult:
        self.calls += 1
        corrected = np.rot90(image, k=-self.quarter_turns).copy()
        return RotationResult(corrected_image=corrected, quarter_turns=self.quarter_turns)


class FakeDetector:
    def __init__(self, boxes=None):
        self.boxes = [GOOD_BOX] if boxes is None else boxes
        self.calls = 0

    def detect(self, image: np.ndarray):
        self.calls += 1
        return list(self.boxes)


class FakeLandmarker:
    def __init__(self, landmarks=GOOD_LANDMARKS):
        self.landmarks = landmarks
        self.calls = 0

    def detect(self, image: np.ndarray, face: FaceBox):
        self.calls += 1
        return np.asarray(self.landmarks).copy()


_DEFAULT_ALIGNED = object()


class FakeAligner:
    def __init__(self, aligned=_DEFAULT_ALIGNED):
        self.aligned = (
            _textured_image()[72:328, 72:328].copy()
            if aligned is _DEFAULT_ALIGNED
            else aligned
        )
        self.calls = 0

    def align(self, image: np.ndarray, landmarks):
        self.calls += 1
        if self.aligned is None:
            return None
        return np.asarray(self.aligned).copy()


class FakePoseEstimator:
    def __init__(self, pose=Pose(yaw=0.0, pitch=0.0, roll=0.0)):
        self.pose = pose
        self.calls = 0

    def estimate(self, image: np.ndarray, face: FaceBox) -> Pose:
        self.calls += 1
        return self.pose


class FakeEmbedder:
    def __init__(
        self,
        vector: np.ndarray | None = None,
        *,
        feature_version: str = "recognizer_v1_1024",
        feature_dim: int = 1024,
    ):
        self.vector = (
            np.arange(1, 1025, dtype=np.float32) if vector is None else vector
        )
        self.feature_version = feature_version
        self.feature_dim = feature_dim
        self.calls = 0

    def extract(self, aligned_face: np.ndarray) -> np.ndarray:
        self.calls += 1
        return np.asarray(self.vector).copy()


def _pipeline(
    *,
    rotator=None,
    detector=None,
    landmarker=None,
    aligner=None,
    pose=None,
    embedder=None,
    max_image_pixels=1_000_000,
) -> FacePipeline:
    return FacePipeline(
        rotator=rotator or FakeRotator(),
        detector=detector or FakeDetector(),
        landmarker=landmarker or FakeLandmarker(),
        aligner=aligner or FakeAligner(),
        pose_estimator=pose or FakePoseEstimator(),
        embedder=embedder or FakeEmbedder(),
        model_versions={
            "face_detector": "test-detector-v1",
            "face_recognizer": "recognizer-v1",
        },
        config_version="unit-test-v1",
        max_image_pixels=max_image_pixels,
    )


@pytest.mark.parametrize("invalid_limit", [0, -1, True, 1.5])
def test_pipeline_requires_positive_integer_pixel_limit(invalid_limit) -> None:
    assert "max_image_pixels" in inspect.signature(FacePipeline).parameters
    with pytest.raises((TypeError, ValueError)):
        FacePipeline(
            rotator=FakeRotator(),
            detector=FakeDetector(),
            landmarker=FakeLandmarker(),
            aligner=FakeAligner(),
            pose_estimator=FakePoseEstimator(),
            embedder=FakeEmbedder(),
            model_versions={},
            config_version="unit-test-v1",
            max_image_pixels=invalid_limit,
        )


def test_pipeline_enforces_pixel_limit_before_inference() -> None:
    rotator = FakeRotator()
    detector = FakeDetector()
    pipeline = FacePipeline(
        rotator=rotator,
        detector=detector,
        landmarker=FakeLandmarker(),
        aligner=FakeAligner(),
        pose_estimator=FakePoseEstimator(),
        embedder=FakeEmbedder(),
        model_versions={},
        config_version="unit-test-v1",
        max_image_pixels=9_999,
    )
    image = np.zeros((100, 100, 3), dtype=np.uint8)

    result = pipeline.analyze_public(image, _profile())

    assert result.success is False
    assert result.reason_codes == (ReasonCode.IMAGE_TOO_LARGE,)
    assert result.original_width == 100
    assert result.original_height == 100
    assert rotator.calls == detector.calls == 0


@pytest.mark.parametrize(
    "image",
    [
        None,
        np.asarray([], dtype=np.uint8),
        np.zeros((10, 10), dtype=np.uint8),
        np.zeros((10, 10, 4), dtype=np.uint8),
        np.zeros((10, 10, 3), dtype=np.float32),
    ],
)
def test_invalid_image_returns_structured_recapture_without_inference(image) -> None:
    detector = FakeDetector()
    result = _pipeline(detector=detector).analyze_public(image, _profile())

    assert result.success is False
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.INVALID_IMAGE,)
    assert result.original_width is None
    assert result.original_height is None
    assert detector.calls == 0


@pytest.mark.parametrize(
    ("boxes", "expected"),
    [
        ([], ReasonCode.NO_FACE),
        (
            [
                GOOD_BOX,
                FaceBox(x1=40.0, y1=40.0, x2=90.0, y2=90.0, confidence=0.8),
            ],
            ReasonCode.MULTIPLE_FACES,
        ),
    ],
)
def test_zero_or_multiple_faces_never_reaches_landmarks(boxes, expected) -> None:
    landmarker = FakeLandmarker()
    result = _pipeline(
        detector=FakeDetector(boxes),
        landmarker=landmarker,
    ).analyze_public(_textured_image(), _profile())

    assert result.reason_codes == (expected,)
    assert result.success is True
    assert result.suggestion is Suggestion.RECAPTURE
    assert landmarker.calls == 0


def test_raw_face_size_is_checked_before_alignment_can_upscale() -> None:
    small = FaceBox(x1=170.0, y1=170.0, x2=230.0, y2=230.0, confidence=0.9)
    aligner = FakeAligner(aligned=np.zeros((1024, 1024, 3), dtype=np.uint8))

    result = _pipeline(
        detector=FakeDetector([small]),
        aligner=aligner,
    ).analyze_public(_textured_image(), _profile())

    assert result.reason_codes == (ReasonCode.FACE_TOO_SMALL,)
    assert result.success is True
    assert aligner.calls == 0


@pytest.mark.parametrize(
    ("box", "landmarks"),
    [
        (
            FaceBox(x1=1.0, y1=100.0, x2=201.0, y2=300.0, confidence=0.9),
            GOOD_LANDMARKS,
        ),
        (
            GOOD_BOX,
            np.asarray(
                [(3.0, 160.0), (250.0, 160.0), (200.0, 205.0), (165.0, 250.0), (235.0, 250.0)],
                dtype=np.float32,
            ),
        ),
    ],
)
def test_box_or_required_landmark_at_image_edge_is_face_cropped(box, landmarks) -> None:
    result = _pipeline(
        detector=FakeDetector([box]),
        landmarker=FakeLandmarker(landmarks),
    ).analyze_public(_textured_image(), _profile())

    assert result.reason_codes == (ReasonCode.FACE_CROPPED,)
    assert result.success is True
    assert result.suggestion is Suggestion.RECAPTURE


@pytest.mark.parametrize(
    "landmarks",
    [
        np.zeros((4, 2), dtype=np.float32),
        np.asarray([(150, 160), (250, 160), (200, math.nan), (165, 250), (235, 250)]),
        np.asarray([(150, 160), (250, 160), (200, math.inf), (165, 250), (235, 250)]),
    ],
)
def test_landmark_output_contract_failures_are_incomplete_system_reviews(
    landmarks,
) -> None:
    result = _pipeline(
        landmarker=FakeLandmarker(landmarks),
    ).analyze_public(_textured_image(), _profile())

    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW


@pytest.mark.parametrize(
    "landmarks",
    [
        GOOD_LANDMARKS[[1, 0, 2, 3, 4]],
        np.asarray([(150, 160), (250, 210), (200, 205), (165, 250), (235, 250)]),
        np.asarray([(150, 160), (250, 160), (200, 205), (190, 250), (195, 250)]),
    ],
)
def test_finite_landmark_topology_failures_request_recapture(landmarks) -> None:
    result = _pipeline(
        landmarker=FakeLandmarker(landmarks),
    ).analyze_public(_textured_image(), _profile())

    assert result.reason_codes == (ReasonCode.LANDMARK_FAILED,)
    assert result.success is True
    assert result.suggestion is Suggestion.RECAPTURE


@pytest.mark.parametrize(
    "aligned",
    [
        None,
        np.zeros((0, 0, 3), dtype=np.uint8),
        np.zeros((100, 100), dtype=np.uint8),
        np.zeros((100, 100, 3), dtype=np.float32),
    ],
)
def test_alignment_output_contract_failure_is_incomplete_system_review(aligned) -> None:
    pose = FakePoseEstimator()
    embedder = FakeEmbedder()
    result = _pipeline(
        aligner=FakeAligner(aligned=aligned),
        pose=pose,
        embedder=embedder,
    ).analyze_public(
        _textured_image(),
        _profile(),
    )

    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert pose.calls == embedder.calls == 0


def test_wrong_sized_uint8_alignment_never_reaches_embedding() -> None:
    embedder = FakeEmbedder()
    aligned = np.full((128, 128, 3), 127, dtype=np.uint8)
    result = _pipeline(
        aligner=FakeAligner(aligned=aligned),
        embedder=embedder,
    ).analyze_public(_textured_image(), _profile())

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert embedder.calls == 0


@pytest.mark.parametrize(
    ("image", "pose", "expected"),
    [
        (np.zeros((400, 400, 3), dtype=np.uint8), Pose(yaw=0.0, pitch=0.0, roll=0.0), ReasonCode.TOO_DARK),
        (np.full((400, 400, 3), 255, dtype=np.uint8), Pose(yaw=0.0, pitch=0.0, roll=0.0), ReasonCode.TOO_BRIGHT),
        (np.full((400, 400, 3), 128, dtype=np.uint8), Pose(yaw=0.0, pitch=0.0, roll=0.0), ReasonCode.TOO_BLURRY),
        (_textured_image(), Pose(yaw=31.0, pitch=0.0, roll=0.0), ReasonCode.BAD_POSE),
        (_textured_image(), Pose(yaw=0.0, pitch=0.0, roll=21.0), ReasonCode.BAD_POSE),
    ],
)
def test_quality_failures_are_structured_and_do_not_extract_embedding(
    image,
    pose,
    expected,
) -> None:
    embedder = FakeEmbedder()
    result = _pipeline(
        pose=FakePoseEstimator(pose),
        embedder=embedder,
    ).analyze_trusted(image, _profile())

    assert expected in result.public.reason_codes
    assert result.public.success is True
    assert result.public.suggestion is Suggestion.RECAPTURE
    assert result.embedding is None
    assert embedder.calls == 0
    assert result.public.quality is not None
    assert all(math.isfinite(metric.value) for metric in result.public.quality.metrics)
    assert {"yaw", "pitch", "roll"}.issubset(
        {metric.name for metric in result.public.quality.metrics}
    )


def test_solid_image_clarity_metrics_are_finite_and_never_high_quality() -> None:
    result = _pipeline().analyze_public(
        np.full((400, 400, 3), 128, dtype=np.uint8),
        _profile(),
    )

    clarity = next(metric for metric in result.quality.metrics if metric.name == "clarity")
    assert math.isfinite(clarity.value)
    assert clarity.passed is False
    assert ReasonCode.TOO_BLURRY in result.reason_codes


def test_clarity_uses_worst_grid_cell_not_one_sharp_quadrant() -> None:
    from face_algorithm.core.quality import clarity_score

    image = np.full((120, 120, 3), 128, dtype=np.uint8)
    checker = ((np.indices((60, 60)).sum(axis=0) % 2) * 255).astype(np.uint8)
    image[:60, :60] = checker[..., None]

    assert clarity_score(image) == pytest.approx(0.0)


def test_legacy_quality_helpers_return_finite_structured_metrics() -> None:
    from models.imgchecker import QualityChecker, QualityOfClarity

    solid = np.full((120, 120, 3), 128, dtype=np.uint8)
    clarity = QualityOfClarity().evaluate(solid)
    checker = QualityChecker(v0=0.0, v1=1.0, v2=254.0, v3=255.0, hw=(112, 112))
    brightness = checker.evaluate_brightness(solid)
    resolution = checker.evaluate_resolution(solid)

    for metric in (clarity, brightness, resolution):
        assert set(metric) == {"value", "passed", "reason"}
        assert math.isfinite(metric["value"])
    assert clarity == {"value": 0.0, "passed": False, "reason": "TOO_BLURRY"}
    assert brightness["passed"] is True
    assert resolution["passed"] is True


def test_success_returns_strict_normalized_1024_embedding_only_to_trusted_call() -> None:
    embedder = FakeEmbedder(vector=np.linspace(1.0, 2.0, 1024, dtype=np.float32))
    pipeline = _pipeline(embedder=embedder)

    trusted = pipeline.analyze_trusted(_textured_image(), _profile())
    public = pipeline.analyze_public(_textured_image(), _profile())

    assert trusted.public.success is True
    assert trusted.public.suggestion is Suggestion.PASS
    assert trusted.embedding is not None
    assert trusted.embedding.dtype == np.float32
    assert trusted.embedding.shape == (1024,)
    assert np.linalg.norm(trusted.embedding) == pytest.approx(1.0, abs=1e-6)
    payload = json.loads(public.model_dump_json())
    serialized = json.dumps(payload, sort_keys=True).casefold()
    assert "embedding" not in serialized
    assert "feature_vector" not in serialized
    assert public.coordinate_space == "corrected_image"
    assert public.box is not None and public.landmarks is not None
    assert public.quality is not None and public.pose is not None
    assert public.processing_steps[-1] == "feature_extraction"
    assert public.original_width == 400
    assert public.original_height == 400
    assert json.loads(public.model_dump_json())["original_width"] == 400


@pytest.mark.parametrize(
    "embedder",
    [
        FakeEmbedder(vector=np.ones(512, dtype=np.float32), feature_dim=512),
        FakeEmbedder(feature_version="recognizer_v0_1024"),
    ],
)
def test_embedding_dimension_or_version_mismatch_is_not_accepted(embedder) -> None:
    result = _pipeline(embedder=embedder).analyze_trusted(
        _textured_image(),
        _profile(),
    )

    assert result.public.reason_codes == (ReasonCode.FEATURE_VERSION_MISMATCH,)
    assert result.public.success is False
    assert result.embedding is None


@pytest.mark.parametrize(
    "vector",
    [
        np.zeros(1024, dtype=np.float32),
        np.full(1024, np.nan, dtype=np.float32),
    ],
)
def test_non_finite_or_zero_embedding_is_feature_failed(vector) -> None:
    result = _pipeline(embedder=FakeEmbedder(vector=vector)).analyze_trusted(
        _textured_image(),
        _profile(),
    )

    assert result.public.reason_codes == (ReasonCode.FEATURE_FAILED,)
    assert result.public.success is False
    assert result.embedding is None


def test_extreme_float32_embedding_cannot_pass_as_a_zero_vector() -> None:
    extreme = np.full(1024, np.finfo(np.float32).max, dtype=np.float32)

    result = _pipeline(embedder=FakeEmbedder(vector=extreme)).analyze_trusted(
        _textured_image(),
        _profile(),
    )

    assert result.public.success is True
    assert result.public.suggestion is Suggestion.PASS
    assert result.public.reason_codes == ()
    assert result.embedding is not None
    assert np.all(np.isfinite(result.embedding))
    assert np.any(result.embedding != 0.0)
    assert np.linalg.norm(result.embedding.astype(np.float64)) == pytest.approx(
        1.0,
        abs=1e-6,
    )


@pytest.mark.parametrize(
    "vector",
    [
        np.ones((1, 1024), dtype=np.float32),
        np.ones(1024, dtype=np.float64),
    ],
)
def test_wrong_embedding_shape_or_dtype_is_feature_failed(vector) -> None:
    result = _pipeline(embedder=FakeEmbedder(vector=vector)).analyze_trusted(
        _textured_image(),
        _profile(),
    )

    assert result.public.reason_codes == (ReasonCode.FEATURE_FAILED,)
    assert result.public.success is False
    assert result.embedding is None


class RaisingLandmarker:
    def detect(self, image: np.ndarray, face: FaceBox):
        raise RuntimeError("synthetic landmark inference failure")


class RaisingAligner:
    def align(self, image: np.ndarray, landmarks):
        raise RuntimeError("synthetic alignment implementation failure")


class RaisingRotator:
    def correct(self, image: np.ndarray):
        raise RuntimeError("synthetic rotation inference failure")


class RaisingDetector:
    def detect(self, image: np.ndarray):
        raise RuntimeError("synthetic detector inference failure")


class RaisingPoseEstimator:
    def estimate(self, image: np.ndarray, face: FaceBox):
        raise RuntimeError("synthetic pose inference failure")


class RaisingEmbedder(FakeEmbedder):
    def extract(self, aligned_face: np.ndarray):
        self.calls += 1
        raise RuntimeError("synthetic embedding inference failure")


@pytest.mark.parametrize(
    "pipeline",
    [
        _pipeline(landmarker=RaisingLandmarker()),
        _pipeline(aligner=RaisingAligner()),
    ],
)
def test_component_exception_is_incomplete_system_review(pipeline) -> None:
    result = pipeline.analyze_public(_textured_image(), _profile())

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)


@pytest.mark.parametrize(
    ("pipeline", "expected_reason"),
    [
        (_pipeline(rotator=RaisingRotator()), ReasonCode.INFERENCE_ERROR),
        (_pipeline(detector=RaisingDetector()), ReasonCode.INFERENCE_ERROR),
        (_pipeline(pose=RaisingPoseEstimator()), ReasonCode.INFERENCE_ERROR),
        (_pipeline(embedder=RaisingEmbedder()), ReasonCode.FEATURE_FAILED),
    ],
)
def test_remaining_component_exceptions_have_stable_system_mapping(
    pipeline,
    expected_reason,
) -> None:
    result = pipeline.analyze_public(_textured_image(), _profile())

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (expected_reason,)


def test_face_align_never_writes_debug_images(tmp_path, monkeypatch) -> None:
    from models.facealign import FaceAlign

    monkeypatch.chdir(tmp_path)
    image = _textured_image()
    aligned = FaceAlign().align(image, GOOD_LANDMARKS.tolist())

    assert aligned.shape == (256, 256, 3)
    assert list(tmp_path.rglob("*")) == []


def test_analysis_never_modifies_callers_image_and_reuses_components() -> None:
    image = _textured_image()
    original = image.copy()
    rotator = FakeRotator()
    detector = FakeDetector()
    pipeline = _pipeline(rotator=rotator, detector=detector)

    first = pipeline.analyze_public(image, _profile())
    second = pipeline.analyze_public(image, _profile())

    assert first.success and second.success
    assert np.array_equal(image, original)
    assert rotator.calls == 2
    assert detector.calls == 2
    assert pipeline.detector is detector


def test_quality_only_analysis_never_extracts_or_exposes_an_embedding() -> None:
    image = _textured_image()
    embedder = FakeEmbedder()
    pipeline = _pipeline(embedder=embedder)

    result = pipeline.analyze_quality(image, _profile())

    assert result.public.success is True
    assert result.public.suggestion is Suggestion.PASS
    assert result.corrected_image is not None
    assert result.aligned_face is not None
    assert not hasattr(result, "embedding")
    assert "feature_extraction" not in result.public.processing_steps
    assert embedder.calls == 0


def test_legacy_extractor_keeps_exact_dataclass_shape_while_delegating() -> None:
    from dataclasses import fields

    from face_feature_extractor import (
        FaceFeatureExtractor,
        FaceInfo,
        FeatureExtractionResult,
    )

    extractor = FaceFeatureExtractor(pipeline=_pipeline(), profile=_profile())
    result = extractor.extract_features(_textured_image())

    assert [field.name for field in fields(FeatureExtractionResult)] == [
        "success",
        "faces",
        "processing_time",
        "error_message",
    ]
    assert [field.name for field in fields(FaceInfo)] == [
        "bbox",
        "landmarks",
        "confidence",
        "quality_scores",
        "feature",
    ]
    assert result.success is True
    assert len(result.faces) == 1
    face = result.faces[0]
    assert isinstance(face.bbox, tuple) and len(face.bbox) == 4
    assert isinstance(face.landmarks, list) and len(face.landmarks) == 5
    assert face.feature is not None and face.feature.shape == (1024,)
    assert face.quality_scores["overall"]["passed"] is True


def test_legacy_detect_keeps_original_image_coordinates_without_rotation() -> None:
    from models.facedetector import Box

    from face_feature_extractor import FaceFeatureExtractor

    rotator = FakeRotator(quarter_turns=1)
    detector = FakeDetector()
    extractor = FaceFeatureExtractor(
        pipeline=_pipeline(rotator=rotator, detector=detector),
        profile=_profile(),
    )

    boxes = extractor.detect_faces(_textured_image())

    assert rotator.calls == 0
    assert detector.calls == 1
    assert len(boxes) == 1 and isinstance(boxes[0], Box)
    assert boxes[0].score == pytest.approx(GOOD_BOX.confidence)


@pytest.mark.parametrize(
    "image",
    [
        None,
        np.zeros((10, 10), dtype=np.uint8),
        np.zeros((10, 10, 3), dtype=np.float32),
    ],
)
def test_legacy_detect_rejects_invalid_images_before_detector(image) -> None:
    from face_feature_extractor import FaceFeatureExtractor

    detector = FakeDetector()
    extractor = FaceFeatureExtractor(
        pipeline=_pipeline(detector=detector),
        profile=_profile(),
    )

    with pytest.raises(ValueError, match="image"):
        extractor.detect_faces(image)

    assert detector.calls == 0


@pytest.mark.parametrize(
    ("method_name", "component_name"),
    [
        ("correct_image_rotation", "rotator"),
        ("detect_faces", "detector"),
        ("extract_landmarks", "landmarker"),
        ("assess_quality", "pose_estimator"),
    ],
)
def test_legacy_direct_image_entries_enforce_pixel_limit_before_inference(
    method_name,
    component_name,
) -> None:
    from face_feature_extractor import FaceFeatureExtractor
    from models.facedetector import Box

    pipeline = _pipeline(max_image_pixels=9_999)
    extractor = FaceFeatureExtractor(pipeline=pipeline, profile=_profile())
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    box = Box(10.0, 10.0, 90.0, 90.0, 0.9)

    with pytest.raises(ValueError, match="max_image_pixels"):
        if method_name == "correct_image_rotation":
            extractor.correct_image_rotation(image)
        elif method_name == "detect_faces":
            extractor.detect_faces(image)
        elif method_name == "extract_landmarks":
            extractor.extract_landmarks(image, [box])
        else:
            extractor.assess_quality(
                image,
                box,
                np.zeros((80, 80, 3), dtype=np.uint8),
            )

    assert getattr(pipeline, component_name).calls == 0


def test_legacy_optional_image_entries_preserve_none_and_skip_models_when_oversized() -> None:
    from face_feature_extractor import FaceFeatureExtractor

    aligner = FakeAligner()
    embedder = FakeEmbedder()
    pipeline = _pipeline(
        aligner=aligner,
        embedder=embedder,
        max_image_pixels=9_999,
    )
    extractor = FaceFeatureExtractor(pipeline=pipeline, profile=_profile())
    image = np.zeros((100, 100, 3), dtype=np.uint8)

    assert extractor.align_face(image, GOOD_LANDMARKS.tolist()) is None
    assert extractor.extract_feature(image) is None
    assert aligner.calls == embedder.calls == 0


@pytest.mark.parametrize(
    ("feature_version", "feature_dim"),
    [
        ("recognizer_v0_512", 1024),
        ("recognizer_v1_1024", 512),
    ],
)
def test_legacy_extract_feature_rejects_metadata_before_embedder_inference(
    feature_version,
    feature_dim,
) -> None:
    from face_feature_extractor import FaceFeatureExtractor

    embedder = FakeEmbedder(
        feature_version=feature_version,
        feature_dim=feature_dim,
    )
    extractor = FaceFeatureExtractor(
        pipeline=_pipeline(embedder=embedder),
        profile=_profile(),
    )

    assert extractor.extract_feature(np.zeros((256, 256, 3), dtype=np.uint8)) is None
    assert embedder.calls == 0


@pytest.mark.parametrize(
    "vector",
    [
        np.ones((1, 1024), dtype=np.float32),
        np.ones(1024, dtype=np.float64),
        np.full(1024, np.nan, dtype=np.float32),
        np.full(1024, np.inf, dtype=np.float32),
        np.zeros(1024, dtype=np.float32),
    ],
)
def test_legacy_extract_feature_rejects_invalid_raw_embedding_contract(vector) -> None:
    from face_feature_extractor import FaceFeatureExtractor

    extractor = FaceFeatureExtractor(
        pipeline=_pipeline(embedder=FakeEmbedder(vector=vector)),
        profile=_profile(),
    )

    assert extractor.extract_feature(np.zeros((256, 256, 3), dtype=np.uint8)) is None


def test_legacy_extract_feature_uses_safe_normalization_without_modifying_inputs() -> None:
    from face_feature_extractor import FaceFeatureExtractor

    vector = np.linspace(1.0, 2.0, 1024, dtype=np.float32)
    vector_before = vector.copy()
    aligned = np.full((256, 256, 3), 127, dtype=np.uint8)
    aligned_before = aligned.copy()
    extractor = FaceFeatureExtractor(
        pipeline=_pipeline(embedder=FakeEmbedder(vector=vector)),
        profile=_profile(),
    )

    feature = extractor.extract_feature(aligned)

    assert feature is not None
    assert feature.shape == (1024,)
    assert feature.dtype == np.float32
    assert np.all(np.isfinite(feature))
    assert np.linalg.norm(feature.astype(np.float64)) == pytest.approx(1.0, abs=1e-6)
    assert np.array_equal(vector, vector_before)
    assert np.array_equal(aligned, aligned_before)


class FixedRotationOutput:
    def __init__(self, corrected: np.ndarray, quarter_turns: int) -> None:
        self.corrected = corrected
        self.quarter_turns = quarter_turns
        self.calls = 0

    def correct(self, image: np.ndarray) -> RotationResult:
        self.calls += 1
        return RotationResult(
            corrected_image=self.corrected.copy(),
            quarter_turns=self.quarter_turns,
        )


@pytest.mark.parametrize(
    ("quarter_turns", "corrected_shape"),
    [
        (0, (200, 120, 3)),
        (1, (120, 200, 3)),
        (2, (200, 120, 3)),
        (3, (120, 200, 3)),
    ],
)
def test_pipeline_rejects_rotation_dimension_mismatch_before_detector(
    quarter_turns,
    corrected_shape,
) -> None:
    image = np.zeros((120, 200, 3), dtype=np.uint8)
    detector = FakeDetector()
    rotator = FixedRotationOutput(
        np.zeros(corrected_shape, dtype=np.uint8),
        quarter_turns,
    )

    result = _pipeline(rotator=rotator, detector=detector).analyze_public(
        image,
        _profile(),
    )

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert detector.calls == 0


def test_pipeline_rejects_rotation_output_over_pixel_limit_before_detector() -> None:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    detector = FakeDetector()
    rotator = FixedRotationOutput(
        np.zeros((100, 101, 3), dtype=np.uint8),
        quarter_turns=0,
    )

    result = _pipeline(
        rotator=rotator,
        detector=detector,
        max_image_pixels=10_000,
    ).analyze_public(image, _profile())

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert detector.calls == 0


def test_build_pipeline_embeds_evidence_from_the_same_loaded_session_snapshot(
    monkeypatch,
) -> None:
    import face_algorithm.core.adapters as adapters

    roles = (
        "rotation_classifier",
        "face_detector",
        "face_landmark_primary",
        "face_landmark_secondary",
        "pose_var",
        "pose_conv",
        "face_recognizer",
    )
    entries = tuple(
        SimpleNamespace(name=name, version="stale-manifest-v0", required=True)
        for name in roles
    )

    class FakeRegistry:
        manifest = SimpleNamespace(models=entries)

        def __init__(self) -> None:
            self.handle_calls: list[str] = []
            self.evidence = {
                name: VerifiedModelEvidence(
                    name=name,
                    version="test-v1",
                    sha256=f"{index:064x}",
                    size=index + 1,
                    backend="onnxruntime",
                    providers=("CPUExecutionProvider",),
                    feature_version=(
                        "recognizer_v1_1024" if name == "face_recognizer" else None
                    ),
                    output_dimension=1024 if name == "face_recognizer" else None,
                )
                for index, name in enumerate(roles, start=1)
            }

        def get(self, name: str):
            raise AssertionError("pipeline builder must consume atomic handles")

        def verified_evidence(self, name: str) -> VerifiedModelEvidence:
            raise AssertionError("pipeline builder must consume atomic handles")

        def get_with_evidence(self, name: str) -> LoadedModelHandle:
            self.handle_calls.append(name)
            return LoadedModelHandle(
                instance=SimpleNamespace(name=name),
                evidence=self.evidence[name],
            )

    registry = FakeRegistry()
    monkeypatch.setattr(adapters, "RotationAdapter", lambda value: value)
    monkeypatch.setattr(adapters, "FaceBoxesAdapter", lambda value: value)
    monkeypatch.setattr(adapters, "Landmark5Adapter", lambda first, second: (first, second))
    monkeypatch.setattr(adapters, "FaceAlignAdapter", lambda: object())
    monkeypatch.setattr(adapters, "PoseAdapter", lambda first, second: (first, second))
    monkeypatch.setattr(adapters, "FaceRecognizerAdapter", lambda value: value)
    config = SimpleNamespace(
        config_version="test-config-v1",
        resources=SimpleNamespace(max_image_pixels=25_000_000),
    )

    pipeline = adapters.build_face_pipeline(config=config, registry=registry)
    original = pipeline.model_evidence["face_recognizer"]

    registry.evidence["face_recognizer"] = VerifiedModelEvidence(
        name="face_recognizer",
        version="replacement",
        sha256="f" * 64,
        size=999,
        backend="onnxruntime",
        providers=("CPUExecutionProvider",),
        feature_version="recognizer_v1_1024",
        output_dimension=1024,
    )

    assert set(pipeline.model_evidence) == set(roles)
    assert registry.handle_calls == list(roles)
    assert dict(pipeline.model_versions) == {name: "test-v1" for name in roles}
    assert pipeline.model_evidence["face_recognizer"] is original
    with pytest.raises(TypeError):
        pipeline.model_evidence["face_recognizer"] = registry.evidence["face_recognizer"]


def test_pipeline_execution_bindings_and_audit_maps_are_sealed_after_init() -> None:
    pipeline = _pipeline()
    original_detector = pipeline.detector
    original_embedder = pipeline.embedder

    with pytest.raises(AttributeError):
        pipeline.detector = object()
    with pytest.raises(AttributeError):
        pipeline.embedder = object()
    with pytest.raises(AttributeError):
        pipeline.analyze_trusted = lambda *_args: None
    with pytest.raises(AttributeError):
        pipeline.config_version = "tampered"
    with pytest.raises(TypeError):
        pipeline.model_versions["face_recognizer"] = "tampered"
    with pytest.raises(TypeError):
        pipeline.model_evidence["face_recognizer"] = object()

    assert pipeline.detector is original_detector
    assert pipeline.embedder is original_embedder


def test_default_extractor_uses_pipeline_and_profile_from_one_runtime_snapshot(
    monkeypatch,
) -> None:
    import face_algorithm.core.adapters as adapters
    import face_feature_extractor as legacy

    assert hasattr(adapters, "get_default_runtime")
    profile_a = object()
    profile_b = object()
    config_a = SimpleNamespace(
        quality_profiles=SimpleNamespace(entry_verify=profile_a),
    )
    config_b = SimpleNamespace(
        quality_profiles=SimpleNamespace(entry_verify=profile_b),
    )
    pipeline_a = _pipeline()
    load_calls = 0

    def load_snapshot():
        nonlocal load_calls
        load_calls += 1
        return config_a if load_calls == 1 else config_b

    monkeypatch.setattr(adapters, "_default_runtime", None, raising=False)
    monkeypatch.setattr(adapters, "load_config", load_snapshot)
    monkeypatch.setattr(adapters, "create_model_registry", lambda *_args: object())
    monkeypatch.setattr(adapters, "build_face_pipeline", lambda **_kwargs: pipeline_a)
    monkeypatch.setattr(legacy, "load_config", lambda: config_b)

    runtime_pipeline, runtime_config = adapters.get_default_runtime()
    extractor = legacy.FaceFeatureExtractor()

    assert runtime_pipeline is pipeline_a
    assert runtime_config is config_a
    assert extractor.pipeline is pipeline_a
    assert extractor.profile is profile_a
    assert load_calls == 1


def test_default_runtime_initialization_is_atomic_under_concurrency(monkeypatch) -> None:
    import face_algorithm.core.adapters as adapters

    assert hasattr(adapters, "get_default_runtime")
    config = SimpleNamespace()
    pipeline = object()
    calls = {"load": 0, "registry": 0, "build": 0}
    calls_lock = threading.Lock()

    def count(name, value):
        with calls_lock:
            calls[name] += 1
        return value

    monkeypatch.setattr(adapters, "_default_runtime", None, raising=False)
    monkeypatch.setattr(adapters, "load_config", lambda: count("load", config))
    monkeypatch.setattr(
        adapters,
        "create_model_registry",
        lambda *_args: count("registry", object()),
    )
    monkeypatch.setattr(
        adapters,
        "build_face_pipeline",
        lambda **_kwargs: count("build", pipeline),
    )

    with ThreadPoolExecutor(max_workers=16) as executor:
        runtimes = list(executor.map(lambda _index: adapters.get_default_runtime(), range(64)))

    assert all(runtime is runtimes[0] for runtime in runtimes)
    assert all(runtime == (pipeline, config) for runtime in runtimes)
    assert calls == {"load": 1, "registry": 1, "build": 1}


def test_legacy_convenience_functions_cache_the_default_extractor(monkeypatch) -> None:
    import face_feature_extractor as legacy

    pipeline = _pipeline()
    calls = 0

    def get_runtime():
        nonlocal calls
        calls += 1
        return pipeline, SimpleNamespace(
            quality_profiles=SimpleNamespace(entry_verify=_profile()),
        )

    monkeypatch.setattr(legacy, "get_default_runtime", get_runtime)
    monkeypatch.setattr(legacy, "_default_extractor", None)

    first = legacy.extract_face_feature(_textured_image())
    second = legacy.extract_face_feature(_textured_image())

    assert first is not None and second is not None
    assert calls == 1
