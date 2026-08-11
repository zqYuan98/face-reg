from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import inspect
import json
from pathlib import Path
from threading import Lock
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from face_algorithm.core.pipeline import FaceQualityAnalysis
from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_registry import ModelRegistry
from face_algorithm.schemas import (
    CardRegion,
    FaceAnalysisResult,
    FaceBox,
    FaceLandmarks,
    FeatureVersion,
    LandmarkPoint,
    ReasonCode,
    Suggestion,
)
from face_algorithm.witness.card_detector import _CardDetectionResult
from face_algorithm.witness.runtime import (
    WitnessBlurRuntime,
    _bind_witness_blur_runtime,
    build_witness_blur_runtime,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FACE = FaceBox(x1=90.0, y1=40.0, x2=150.0, y2=120.0, confidence=0.99)
LANDMARKS = FaceLandmarks(
    points=(
        LandmarkPoint(x=108.0, y=68.0),
        LandmarkPoint(x=132.0, y=68.0),
        LandmarkPoint(x=120.0, y=84.0),
        LandmarkPoint(x=110.0, y=102.0),
        LandmarkPoint(x=130.0, y=102.0),
    )
)
CARD = CardRegion(
    corners=(
        LandmarkPoint(x=75.0, y=145.0),
        LandmarkPoint(x=165.0, y=145.0),
        LandmarkPoint(x=165.0, y=195.0),
        LandmarkPoint(x=75.0, y=195.0),
    ),
    confidence=0.95,
)


def _image(height=240, width=240) -> np.ndarray:
    y, x = np.indices((height, width))
    return np.ascontiguousarray(
        np.stack(
            (
                (20 + x % 190).astype(np.uint8),
                (30 + y % 180).astype(np.uint8),
                (40 + (x + y) % 170).astype(np.uint8),
            ),
            axis=2,
        )
    )


def _public(
    *,
    suggestion=Suggestion.PASS,
    reasons=(),
    success=True,
    box=FACE,
    landmarks=LANDMARKS,
    turns=0,
):
    return FaceAnalysisResult(
        success=success,
        suggestion=suggestion,
        reason_codes=reasons,
        model_versions={"face_detector": "test-face-v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="test-v1",
        processing_time_ms=1.0,
        trace_id="face-trace",
        original_width=240,
        original_height=240,
        box=box,
        landmarks=landmarks,
        quality=None,
        pose=None,
        rotation_quarter_turns=turns,
        processing_steps=("rotation", "detection", "landmarks", "quality"),
    )


class FakeFacePipeline:
    def __init__(self, analysis=None):
        self.analysis = analysis or FaceQualityAnalysis(
            public=_public(),
            corrected_image=_image(),
            aligned_face=np.zeros((256, 256, 3), np.uint8),
        )
        self.model_versions = {"face_detector": "test-face-v1"}
        self.calls = 0

    def analyze_quality(self, _image, _profile):
        self.calls += 1
        return self.analysis


class FakeMatting:
    def __init__(self, alpha=None, error=None):
        self.alpha = alpha
        if self.alpha is None:
            self.alpha = np.zeros((240, 240), dtype=np.float32)
            self.alpha[20:230, 45:195] = 1.0
        self.error = error
        self.calls = 0
        self.model_info = SimpleNamespace(version="test-modnet-v1")

    def matte(self, _image):
        self.calls += 1
        if self.error:
            raise self.error
        return self.alpha.copy()


class FakeCardDetector:
    def __init__(self, result=None, error=None):
        self.result = result or _CardDetectionResult(
            cards=(CARD,), candidate_count=1, reason_code=None
        )
        self.error = error
        self.calls = 0

    def __call__(self, _image, _face, _subject, _options):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _runtime(*, face=None, matte=None, detector=None, config=None):
    config = config or load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    return _bind_witness_blur_runtime(
        face_pipeline=face or FakeFacePipeline(),
        matting_runtime=matte or FakeMatting(),
        card_detector=detector or FakeCardDetector(),
        config=config,
    )


def test_success_returns_safe_original_size_image_and_card_summaries() -> None:
    image = _image()
    original = image.copy()

    result = _runtime().blur(image)

    assert result.success is True
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.image is not None
    assert result.image.shape == image.shape
    assert result.image.dtype == np.uint8
    assert result.image.flags.c_contiguous
    assert result.card_regions == (CARD,)
    assert result.card_candidate_count == 1
    assert 0.0 < result.protected_area_ratio <= 1.0
    np.testing.assert_array_equal(image, original)


@pytest.mark.parametrize(
    ("reason", "suggestion"),
    [
        (ReasonCode.NO_FACE, Suggestion.RECAPTURE),
        (ReasonCode.MULTIPLE_FACES, Suggestion.RECAPTURE),
        (ReasonCode.FACE_TOO_SMALL, Suggestion.RECAPTURE),
    ],
)
def test_face_quality_failure_is_preserved_and_later_models_are_skipped(
    reason, suggestion
) -> None:
    analysis = FaceQualityAnalysis(
        public=_public(suggestion=suggestion, reasons=(reason,), box=None, landmarks=None),
        corrected_image=_image(),
        aligned_face=None,
    )
    matting = FakeMatting()
    detector = FakeCardDetector()

    result = _runtime(face=FakeFacePipeline(analysis), matte=matting, detector=detector).blur(
        _image()
    )

    assert result.success is False
    assert result.suggestion is suggestion
    assert result.reason_codes == (reason,)
    assert result.image is None
    assert matting.calls == 0
    assert detector.calls == 0


@pytest.mark.parametrize(
    "reason",
    [
        ReasonCode.CARD_NOT_FOUND,
        ReasonCode.CARD_TOO_SMALL,
        ReasonCode.CARD_CROPPED,
        ReasonCode.CARD_DETECTION_UNCERTAIN,
    ],
)
def test_card_detection_failures_request_recapture_without_partial_image(reason) -> None:
    detector = FakeCardDetector(
        _CardDetectionResult(cards=(), candidate_count=2, reason_code=reason)
    )

    result = _runtime(detector=detector).blur(_image())

    assert result.success is False
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (reason,)
    assert result.image is None
    assert result.card_candidate_count == 2


def test_matting_or_internal_detection_failure_is_hard_review() -> None:
    matting_result = _runtime(matte=FakeMatting(error=RuntimeError("secret"))).blur(
        _image()
    )
    detector_result = _runtime(
        detector=FakeCardDetector(error=RuntimeError("private"))
    ).blur(_image())

    assert matting_result.suggestion is Suggestion.REVIEW
    assert matting_result.reason_codes == (ReasonCode.MATTING_FAILED,)
    assert detector_result.suggestion is Suggestion.REVIEW
    assert detector_result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert "secret" not in repr(matting_result)
    assert "private" not in repr(detector_result)


def test_malformed_matting_output_is_review() -> None:
    result = _runtime(
        matte=FakeMatting(np.full((240, 240), np.nan, dtype=np.float32))
    ).blur(_image())

    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.MATTING_FAILED,)
    assert result.image is None


def test_oversized_input_is_rejected_before_large_allocation_or_model_inference() -> None:
    storage = np.zeros((1,), dtype=np.uint8)
    oversized = np.lib.stride_tricks.as_strided(
        storage, shape=(5_000, 5_001, 3), strides=(0, 0, 0), writeable=False
    )
    face = FakeFacePipeline()
    matte = FakeMatting()
    detector = FakeCardDetector()

    result = _runtime(face=face, matte=matte, detector=detector).blur(oversized)

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.IMAGE_TOO_LARGE,)
    assert result.original_width == 5_001
    assert result.original_height == 5_000
    assert face.calls == matte.calls == detector.calls == 0


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        np.zeros((0, 10, 3), dtype=np.uint8),
        np.zeros((10, 10), dtype=np.uint8),
        np.zeros((10, 10, 3), dtype=np.float32),
    ],
)
def test_invalid_decoded_image_is_rejected_before_models(invalid) -> None:
    face = FakeFacePipeline()
    matte = FakeMatting()
    detector = FakeCardDetector()

    result = _runtime(face=face, matte=matte, detector=detector).blur(invalid)

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.INVALID_IMAGE,)
    assert face.calls == matte.calls == detector.calls == 0


def test_exact_pixel_limit_reaches_inference() -> None:
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    config = config.model_copy(
        update={
            "witness": config.witness.model_copy(
                update={"max_witness_pixels": 57_600}
            )
        }
    )
    face = FakeFacePipeline()

    result = _runtime(face=face, config=config).blur(_image())

    assert result.success is True
    assert face.calls == 1


def test_one_pixel_above_witness_limit_is_rejected_before_models() -> None:
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    config = config.model_copy(
        update={
            "witness": config.witness.model_copy(
                update={"max_witness_pixels": 57_600}
            )
        }
    )
    storage = np.zeros((1,), dtype=np.uint8)
    over = np.lib.stride_tricks.as_strided(
        storage, shape=(1, 57_601, 3), strides=(0, 0, 0), writeable=False
    )
    face = FakeFacePipeline()
    matte = FakeMatting()
    detector = FakeCardDetector()

    result = _runtime(
        face=face,
        matte=matte,
        detector=detector,
        config=config,
    ).blur(over)

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.IMAGE_TOO_LARGE,)
    assert result.original_width == 57_601
    assert result.original_height == 1
    assert face.calls == matte.calls == detector.calls == 0


def test_rotated_processing_is_mapped_back_to_original_shape_and_coordinates() -> None:
    original = _image(180, 240)
    corrected = cv2.rotate(original, cv2.ROTATE_90_CLOCKWISE)
    face = FaceBox(x1=50.0, y1=40.0, x2=110.0, y2=120.0, confidence=0.99)
    public = _public(box=face, turns=1).model_copy(
        update={"original_width": 240, "original_height": 180}
    )
    analysis = FaceQualityAnalysis(
        public=public,
        corrected_image=corrected,
        aligned_face=np.zeros((256, 256, 3), np.uint8),
    )
    alpha = np.zeros(corrected.shape[:2], np.float32)
    alpha[20:235, 20:170] = 1.0
    card = CardRegion(
        corners=tuple(
            LandmarkPoint(x=float(x), y=float(y))
            for x, y in ((45, 145), (135, 145), (135, 195), (45, 195))
        ),
        confidence=0.9,
    )
    detector = FakeCardDetector(
        _CardDetectionResult(cards=(card,), candidate_count=1, reason_code=None)
    )

    result = _runtime(
        face=FakeFacePipeline(analysis), matte=FakeMatting(alpha), detector=detector
    ).blur(original)

    assert result.image is not None
    assert result.image.shape == original.shape
    assert all(
        0 <= point.x < original.shape[1] and 0 <= point.y < original.shape[0]
        for point in result.card_regions[0].corners
    )


def test_public_contract_does_not_expose_biometrics_alpha_or_model_objects() -> None:
    result = _runtime().blur(_image())
    names = {item.name.casefold() for item in fields(result)}
    payload = repr(result).casefold() + json.dumps(
        {
            "reasons": [reason.value for reason in result.reason_codes],
            "models": dict(result.model_versions),
        },
        sort_keys=True,
    ).casefold()

    assert not ({"embedding", "feature", "alpha", "model", "session"} & names)
    assert "embedding" not in payload
    assert "ndarray" not in repr(result)


def test_runtime_is_immutable_and_safe_for_concurrent_calls() -> None:
    runtime = _runtime()
    with pytest.raises((AttributeError, TypeError)):
        runtime._blur_pixels = lambda *_args: None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(runtime.blur, [_image() for _ in range(8)]))

    assert all(result.success for result in results)
    assert len({result.trace_id for result in results}) == len(results)


def test_one_runtime_serializes_memory_intensive_witness_requests() -> None:
    class TrackingFacePipeline(FakeFacePipeline):
        def __init__(self):
            super().__init__()
            self.guard = Lock()
            self.active = 0
            self.max_active = 0

        def analyze_quality(self, image, profile):
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.01)
                return super().analyze_quality(image, profile)
            finally:
                with self.guard:
                    self.active -= 1

    face = TrackingFacePipeline()
    runtime = _runtime(face=face)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(runtime.blur, [_image() for _ in range(4)]))

    assert all(result.success for result in results)
    assert face.max_active == 1


def test_public_builder_only_accepts_config_and_registry() -> None:
    signature = inspect.signature(build_witness_blur_runtime)
    assert list(signature.parameters) == ["config", "registry"]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    with pytest.raises(TypeError, match="unexpected keyword"):
        build_witness_blur_runtime(
            config=object(), registry=object(), card_detector=object()
        )


def test_runtime_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError, match="build_witness_blur_runtime"):
        WitnessBlurRuntime()


def test_builder_wires_owned_quality_and_matting_runtimes(monkeypatch) -> None:
    import face_algorithm.witness.runtime as runtime_module

    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    registry = object.__new__(ModelRegistry)
    face = FakeFacePipeline()
    matte = FakeMatting()
    calls = []

    monkeypatch.setattr(
        runtime_module,
        "build_owned_face_quality_pipeline",
        lambda *, config, registry: calls.append(("face", registry)) or face,
    )
    monkeypatch.setattr(
        runtime_module,
        "build_portrait_matting_runtime",
        lambda *, config, registry: calls.append(("matte", registry)) or matte,
    )

    runtime = build_witness_blur_runtime(config=config, registry=registry)

    assert isinstance(runtime, WitnessBlurRuntime)
    assert calls == [("matte", registry), ("face", registry)]
