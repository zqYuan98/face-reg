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

from face_algorithm.core.face_selection import FaceCandidateBatch
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
    _build_owned_witness_face_probe,
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
    "reason",
    [
        ReasonCode.NO_FACE,
        ReasonCode.FACE_TOO_SMALL,
    ],
)
def test_no_face_or_identity_photo_size_gate_does_not_block_reliable_subject(
    reason,
) -> None:
    analysis = FaceQualityAnalysis(
        public=_public(
            suggestion=Suggestion.RECAPTURE,
            reasons=(reason,),
            box=None,
            landmarks=None,
        ),
        corrected_image=_image(),
        aligned_face=None,
    )
    matting = FakeMatting()
    detector = FakeCardDetector()

    result = _runtime(face=FakeFacePipeline(analysis), matte=matting, detector=detector).blur(
        _image()
    )

    assert result.success is True
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.image is not None
    assert matting.calls == 1
    assert detector.calls == 0


def test_true_multiple_face_failure_is_preserved_and_later_models_are_skipped() -> None:
    analysis = FaceQualityAnalysis(
        public=_public(
            suggestion=Suggestion.RECAPTURE,
            reasons=(ReasonCode.MULTIPLE_FACES,),
            box=None,
            landmarks=None,
        ),
        corrected_image=_image(),
        aligned_face=None,
    )
    matting = FakeMatting()
    detector = FakeCardDetector()

    result = _runtime(
        face=FakeFacePipeline(analysis), matte=matting, detector=detector
    ).blur(_image())

    assert result.success is False
    assert result.reason_codes == (ReasonCode.MULTIPLE_FACES,)
    assert matting.calls == detector.calls == 0


@pytest.mark.parametrize(
    "reason",
    [
        ReasonCode.CARD_NOT_FOUND,
        ReasonCode.CARD_TOO_SMALL,
        ReasonCode.CARD_CROPPED,
        ReasonCode.CARD_DETECTION_UNCERTAIN,
    ],
)
def test_card_detection_failures_degrade_to_person_only_output(reason) -> None:
    detector = FakeCardDetector(
        _CardDetectionResult(cards=(), candidate_count=2, reason_code=reason)
    )

    result = _runtime(detector=detector).blur(_image())

    assert result.success is True
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.image is not None
    assert result.card_regions == ()
    assert result.card_candidate_count == 2


def test_detector_reason_does_not_discard_a_trusted_protected_card() -> None:
    detector = FakeCardDetector(
        _CardDetectionResult(
            cards=(CARD,),
            candidate_count=2,
            reason_code=ReasonCode.CARD_TOO_SMALL,
            protection_cards=(CARD,),
        )
    )

    result = _runtime(detector=detector).blur(_image())

    assert result.success is True
    assert result.reason_codes == ()
    assert result.card_regions == (CARD,)


def test_near_full_person_matte_is_rejected_instead_of_returning_clear_image() -> None:
    alpha = np.zeros((240, 240), np.float32)
    alpha[:220, :230] = 1.0  # 87.85%: below component max, above person budget.

    result = _runtime(matte=FakeMatting(alpha)).blur(_image())

    assert result.success is False
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.MATTING_LOW_CONFIDENCE,)
    assert result.image is None


def test_native_two_stage_face_probe_uses_protection_card_context() -> None:
    secondary = FaceBox(
        x1=100.0,
        y1=155.0,
        x2=130.0,
        y2=190.0,
        confidence=0.99,
    )

    class Rotator:
        def correct(self, image):
            return SimpleNamespace(
                corrected_image=np.ascontiguousarray(image.copy()),
                quarter_turns=0,
            )

    class Detector:
        def detect(self, _image):
            return (FACE, secondary)

    class Landmarker:
        def detect(self, _image, box):
            return np.asarray(
                (
                    (box.x1 + 0.30 * box.width, box.y1 + 0.32 * box.height),
                    (box.x1 + 0.70 * box.width, box.y1 + 0.32 * box.height),
                    (box.x1 + 0.50 * box.width, box.y1 + 0.52 * box.height),
                    (box.x1 + 0.34 * box.width, box.y1 + 0.75 * box.height),
                    (box.x1 + 0.66 * box.width, box.y1 + 0.75 * box.height),
                ),
                np.float32,
            )

    class NativeFacePipeline:
        rotator = Rotator()
        detector = Detector()
        landmarker = Landmarker()
        model_versions = {"face_detector": "test-face-v1"}

        def analyze_quality(self, *_args):
            raise AssertionError("witness must not invoke full identity quality")

    protected = FakeCardDetector(
        _CardDetectionResult(
            cards=(CARD,),
            candidate_count=1,
            reason_code=None,
            protection_cards=(CARD,),
        )
    )
    without_context = FakeCardDetector(
        _CardDetectionResult(
            cards=(),
            candidate_count=0,
            reason_code=ReasonCode.CARD_NOT_FOUND,
            protection_cards=(),
        )
    )
    oversized_context = CardRegion(
        corners=tuple(
            LandmarkPoint(x=float(x), y=float(y))
            for x, y in ((0, 130), (200, 130), (200, 239), (0, 239))
        ),
        confidence=0.99,
    )
    over_budget = FakeCardDetector(
        _CardDetectionResult(
            cards=(),
            candidate_count=1,
            reason_code=ReasonCode.CARD_DETECTION_UNCERTAIN,
            protection_cards=(oversized_context,),
        )
    )

    protected_result = _runtime(
        face=NativeFacePipeline(), detector=protected
    ).blur(_image())
    ambiguous_result = _runtime(
        face=NativeFacePipeline(), detector=without_context
    ).blur(_image())
    over_budget_result = _runtime(
        face=NativeFacePipeline(), detector=over_budget
    ).blur(_image())

    assert protected_result.success is True
    assert protected_result.card_regions == (CARD,)
    assert ambiguous_result.success is False
    assert ambiguous_result.reason_codes == (ReasonCode.MULTIPLE_FACES,)
    assert over_budget_result.success is False
    assert over_budget_result.reason_codes == (ReasonCode.MULTIPLE_FACES,)


def test_saturated_native_face_batch_fails_before_matting_and_cards() -> None:
    class Pipeline:
        model_versions = {
            "rotation_classifier": "rotation-v1",
            "face_detector": "detector-v1",
            "face_landmark_primary": "landmark-primary-v1",
            "face_landmark_secondary": "landmark-secondary-v1",
        }
        rotator = SimpleNamespace(
            correct=lambda image: SimpleNamespace(
                corrected_image=np.ascontiguousarray(image.copy()),
                quarter_turns=0,
            )
        )
        detector = SimpleNamespace(
            detect=lambda _image: FaceCandidateBatch(
                candidates=(FACE,),
                saturated=True,
                raw_candidate_count=65,
            )
        )
        landmarker = SimpleNamespace(
            detect=lambda *_args: (_ for _ in ()).throw(
                AssertionError("saturated batches must not run landmarks")
            )
        )

        def analyze_quality(self, *_args):
            raise AssertionError("native witness probe must not run quality")

    matting = FakeMatting()
    detector = FakeCardDetector()

    result = _runtime(
        face=Pipeline(),
        matte=matting,
        detector=detector,
    ).blur(_image())

    assert result.success is False
    assert result.reason_codes == (ReasonCode.MULTIPLE_FACES,)
    assert matting.calls == 0
    assert detector.calls == 0


def test_matting_failure_is_hard_but_card_detector_exception_is_optional() -> None:
    matting_result = _runtime(matte=FakeMatting(error=RuntimeError("secret"))).blur(
        _image()
    )
    detector_result = _runtime(
        detector=FakeCardDetector(error=RuntimeError("private"))
    ).blur(_image())

    assert matting_result.suggestion is Suggestion.REVIEW
    assert matting_result.reason_codes == (ReasonCode.MATTING_FAILED,)
    assert detector_result.suggestion is Suggestion.PASS
    assert detector_result.reason_codes == ()
    assert detector_result.image is not None
    assert "secret" not in repr(matting_result)
    assert "private" not in repr(detector_result)


def test_malformed_card_detector_output_also_degrades_to_person_only() -> None:
    malformed = _CardDetectionResult(
        cards=[],  # type: ignore[arg-type]
        candidate_count=0,
        reason_code=None,
        protection_cards=(),
    )

    result = _runtime(detector=FakeCardDetector(result=malformed)).blur(_image())

    assert result.success is True
    assert result.reason_codes == ()
    assert result.card_regions == ()


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


def test_builder_wires_owned_witness_probe_and_matting_runtimes(monkeypatch) -> None:
    import face_algorithm.witness.runtime as runtime_module

    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    registry = object.__new__(ModelRegistry)
    face = FakeFacePipeline()
    matte = FakeMatting()
    calls = []

    monkeypatch.setattr(
        runtime_module,
        "_build_owned_witness_face_probe",
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


def test_owned_witness_probe_loads_only_models_it_executes(monkeypatch) -> None:
    import face_algorithm.witness.runtime as runtime_module

    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    source = object.__new__(ModelRegistry)
    requested: list[tuple[str, ...]] = []

    class Isolated:
        def get_with_evidence(self, name):
            return SimpleNamespace(
                instance=f"session:{name}",
                evidence=SimpleNamespace(version=f"{name}-v1"),
            )

    monkeypatch.setattr(
        ModelRegistry,
        "spawn_isolated",
        lambda self, names: requested.append(tuple(names)) or Isolated(),
    )
    monkeypatch.setattr(
        runtime_module,
        "RotationAdapter",
        lambda session: ("rotation", session),
    )
    monkeypatch.setattr(
        runtime_module,
        "FaceBoxesAdapter",
        lambda session: ("detector", session),
    )
    monkeypatch.setattr(
        runtime_module,
        "Landmark5Adapter",
        lambda primary, secondary: ("landmarks", primary, secondary),
    )

    probe = _build_owned_witness_face_probe(config=config, registry=source)

    expected = (
        "rotation_classifier",
        "face_detector",
        "face_landmark_primary",
        "face_landmark_secondary",
    )
    assert requested == [expected]
    assert set(probe.model_versions) == set(expected)
    assert "pose_var" not in probe.model_versions
    assert "pose_conv" not in probe.model_versions
    assert "face_recognizer" not in probe.model_versions
    assert not hasattr(probe, "analyze_quality")
