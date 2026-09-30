from __future__ import annotations

from dataclasses import fields
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from face_algorithm.core.pipeline import FaceQualityAnalysis
from face_algorithm.portrait.generator import (
    PortraitGeneratorRuntime,
    _bind_portrait_generator,
    build_portrait_generator,
    encode_portrait_image,
)
from face_algorithm.portrait.runtime import PortraitMattingDisabledError
from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_registry import ModelRegistry
from face_algorithm.schemas import (
    FaceAnalysisResult,
    FaceBox,
    FaceLandmarks,
    FeatureVersion,
    LandmarkPoint,
    ReasonCode,
    Suggestion,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FACE = FaceBox(x1=150.0, y1=128.0, x2=330.0, y2=435.2, confidence=0.99)
LANDMARKS = FaceLandmarks(
    points=(
        LandmarkPoint(x=200.0, y=256.0),
        LandmarkPoint(x=280.0, y=256.0),
        LandmarkPoint(x=240.0, y=320.0),
        LandmarkPoint(x=205.0, y=380.0),
        LandmarkPoint(x=275.0, y=380.0),
    )
)


def _template(**updates):
    template = load_config(PROJECT_ROOT / "config" / "algorithm.yaml").portrait_template
    return template.model_copy(update={
        "mode": "head_shoulders",
        "version": "head-shoulders-480x640-v1",
        **updates,
    })


def _image() -> np.ndarray:
    y, x = np.indices((640, 480))
    return np.stack(
        (
            (30 + x % 180).astype(np.uint8),
            (50 + y % 160).astype(np.uint8),
            (70 + (x + y) % 140).astype(np.uint8),
        ),
        axis=2,
    )


def _alpha() -> np.ndarray:
    alpha = np.zeros((640, 480), dtype=np.float32)
    alpha[52:640, 80:400] = 1.0
    return alpha


def _public_analysis(
    *,
    suggestion: Suggestion = Suggestion.PASS,
    reasons: tuple[ReasonCode, ...] = (),
    success: bool = True,
    box: FaceBox | None = FACE,
    landmarks: FaceLandmarks | None = LANDMARKS,
) -> FaceAnalysisResult:
    return FaceAnalysisResult(
        success=success,
        suggestion=suggestion,
        reason_codes=reasons,
        model_versions={"face_detector": "test-v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="unit-v1",
        processing_time_ms=1.0,
        trace_id="face-trace",
        original_width=480,
        original_height=640,
        box=box,
        landmarks=landmarks,
        quality=None,
        pose=None,
        rotation_quarter_turns=0,
        processing_steps=("rotation", "detection", "landmarks", "quality"),
    )


class FakeFacePipeline:
    def __init__(self, analysis: FaceQualityAnalysis | None = None) -> None:
        self.analysis = analysis or FaceQualityAnalysis(
            public=_public_analysis(),
            corrected_image=_image(),
            aligned_face=np.zeros((256, 256, 3), dtype=np.uint8),
        )
        self.model_versions = {"face_detector": "test-v1"}
        self.calls = 0

    def analyze_quality(self, image: np.ndarray, profile: object) -> FaceQualityAnalysis:
        self.calls += 1
        return self.analysis


class FakeMatting:
    def __init__(self, alpha: np.ndarray | None = None, error: Exception | None = None):
        self.alpha = _alpha() if alpha is None else alpha
        self.error = error
        self.calls = 0
        self.model_info = SimpleNamespace(
            name="portrait_matting",
            version="test-modnet-v1",
            sha256="a" * 64,
            size=123,
            backend="pytorch",
            providers=("CPUExecutionProvider",),
        )

    def matte(self, image: np.ndarray) -> np.ndarray:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.alpha.copy()


def _runtime(
    *,
    alpha: np.ndarray | None = None,
    face_pipeline: FakeFacePipeline | None = None,
    matting: FakeMatting | None = None,
    template=None,
) -> PortraitGeneratorRuntime:
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    config = config.model_copy(update={"portrait_template": template or _template()})
    return _bind_portrait_generator(
        face_pipeline=face_pipeline or FakeFacePipeline(),
        matting_runtime=matting or FakeMatting(alpha),
        config=config,
    )


def test_default_geometry_is_exact_uniform_scale_and_translation() -> None:
    result = _runtime().generate(_image())

    assert result.success is True
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.image is not None
    assert result.image.shape == (640, 480, 3)
    assert result.placement is not None
    assert result.placement.scale == pytest.approx(1.0, abs=1e-6)
    assert result.placement.translate_x == pytest.approx(0.0, abs=1e-6)
    assert result.placement.translate_y == pytest.approx(0.0, abs=1e-6)
    np.testing.assert_allclose(
        np.asarray(result.placement.affine),
        np.asarray(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))),
        rtol=0.0,
        atol=1e-6,
    )


def test_output_is_contiguous_uint8_white_background_and_input_is_unchanged() -> None:
    image = _image()
    original = image.copy()

    result = _runtime().generate(image)

    assert np.array_equal(image, original)
    assert result.image is not None
    assert result.image.dtype == np.uint8
    assert result.image.flags.c_contiguous
    assert np.array_equal(result.image[10, 10], np.asarray([255, 255, 255]))
    np.testing.assert_array_equal(result.image[300, 240], image[300, 240])


def test_half_transparent_black_is_composited_in_linear_light() -> None:
    image = np.zeros((640, 480, 3), dtype=np.uint8)
    alpha = _alpha() * np.float32(0.5)
    template = _template(
        min_face_alpha_mean=0.40,
        min_face_alpha_coverage=0.90,
        alpha_threshold=0.50,
    )
    analysis = FaceQualityAnalysis(
        public=_public_analysis(),
        corrected_image=image.copy(),
        aligned_face=np.zeros((256, 256, 3), dtype=np.uint8),
    )

    result = _runtime(
        alpha=alpha,
        face_pipeline=FakeFacePipeline(analysis),
        template=template,
    ).generate(image)

    assert result.image is not None
    assert result.image[300, 240].tolist() == pytest.approx([188, 188, 188], abs=1)


def test_component_overlapping_face_wins_over_larger_disconnected_blob() -> None:
    alpha = _alpha()
    alpha[:, :] = 0.0
    alpha[100:640, 110:370] = 1.0
    alpha[0:640, 0:70] = 1.0  # distractor reaches more rows but never overlaps the face

    result = _runtime(alpha=alpha).generate(_image())

    assert result.image is not None
    assert np.array_equal(result.image[300, 30], np.asarray([255, 255, 255]))
    assert not np.array_equal(result.image[300, 240], np.asarray([255, 255, 255]))


def test_small_internal_matting_hole_is_repaired_without_a_white_pinpoint() -> None:
    image = _image()
    alpha = _alpha()
    alpha[299:302, 239:242] = 0.0

    result = _runtime(alpha=alpha).generate(image)

    assert result.image is not None
    np.testing.assert_allclose(
        result.image[300, 240].astype(np.int16),
        image[300, 240].astype(np.int16),
        rtol=0.0,
        atol=2,
    )


@pytest.mark.parametrize("edge", ["left", "right"])
def test_clothing_touching_side_is_allowed(edge: str) -> None:
    alpha = _alpha()
    if edge == "left":
        alpha[52:640, 0:80] = 1.0
    else:
        alpha[52:640, 400:480] = 1.0

    result = _runtime(alpha=alpha).generate(_image())

    assert result.success is True
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.image is not None


def test_source_subject_touching_only_bottom_is_allowed_for_torso_continuation() -> None:
    result = _runtime(alpha=_alpha()).generate(_image())

    assert result.success is True
    assert result.image is not None


def test_insufficient_subject_depth_below_face_requests_recapture() -> None:
    alpha = np.zeros((640, 480), dtype=np.float32)
    alpha[52:500, 80:400] = 1.0

    result = _runtime(alpha=alpha).generate(_image())

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.SHOULDERS_NOT_VISIBLE,)
    assert result.image is None


def test_one_pixel_tail_cannot_satisfy_the_shoulder_visibility_contract() -> None:
    alpha = np.zeros((640, 480), dtype=np.float32)
    alpha[100:436, 150:330] = 1.0
    alpha[435:640, 239:240] = 1.0

    result = _runtime(alpha=alpha).generate(_image())

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.SHOULDERS_NOT_VISIBLE,)
    assert result.image is None


def test_connected_soft_subject_tail_touching_side_is_allowed() -> None:
    alpha = _alpha()
    alpha[300:303, 0:80] = 0.49

    result = _runtime(alpha=alpha).generate(_image())

    assert result.success is True
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.image is not None


def test_sparse_hard_tail_touching_top_does_not_change_placement_geometry() -> None:
    alpha = _alpha()
    alpha[0:52, 239:240] = 1.0

    result = _runtime(alpha=alpha).generate(_image())

    assert result.success is True
    assert result.reason_codes == ()
    assert result.image is not None


def test_connected_hard_tail_below_effective_top_coverage_does_not_fail_geometry() -> None:
    alpha = _alpha()
    # A 9px connected tail exceeds the 5% face-row threshold but is not a
    # meaningful head top within the expanded ROI.
    alpha[0:52, 236:245] = 1.0

    result = _runtime(alpha=alpha).generate(_image())

    assert result.success is True
    assert result.reason_codes == ()
    assert result.image is not None


def test_true_head_top_crop_requests_recapture() -> None:
    face = FaceBox(
        x1=150.0,
        y1=5.0,
        x2=330.0,
        y2=210.0,
        confidence=0.99,
    )
    analysis = FaceQualityAnalysis(
        public=_public_analysis(box=face),
        corrected_image=_image(),
        aligned_face=np.zeros((256, 256, 3), dtype=np.uint8),
    )
    alpha = np.zeros((640, 480), dtype=np.float32)
    alpha[:, 80:400] = 1.0

    result = _runtime(
        alpha=alpha,
        face_pipeline=FakeFacePipeline(analysis),
    ).generate(_image())

    assert result.success is False
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.SUBJECT_CROPPED,)
    assert result.image is None


@pytest.mark.parametrize("failure", ["mean", "coverage"])
def test_low_face_alpha_quality_requests_recapture(failure: str) -> None:
    alpha = _alpha()
    template = _template()
    if failure == "mean":
        alpha[128:436, 150:330] = 0.60
    else:
        alpha[128:436:2, 150:330] = 0.10
        template = template.model_copy(update={"min_face_alpha_mean": 0.0})

    result = _runtime(alpha=alpha, template=template).generate(_image())

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.MATTING_LOW_CONFIDENCE,)
    assert result.image is None


def test_subject_area_below_configured_minimum_requests_recapture() -> None:
    alpha = np.zeros((640, 480), dtype=np.float32)
    alpha[128:620, 150:330] = 1.0
    template = _template(min_subject_area_ratio=0.40)

    result = _runtime(alpha=alpha, template=template).generate(_image())

    assert result.reason_codes == (ReasonCode.MATTING_LOW_CONFIDENCE,)
    assert result.suggestion is Suggestion.RECAPTURE


def test_required_face_height_is_checked_before_matting() -> None:
    required_height = 205.0
    scale_alpha = np.zeros((640, 480), dtype=np.float32)
    scale_alpha[120:540, 150:330] = 1.0
    exact_face = FaceBox(
        x1=180.0,
        y1=180.0,
        x2=300.0,
        y2=180.0 + required_height,
        confidence=0.99,
    )
    exact_analysis = FaceQualityAnalysis(
        public=_public_analysis(box=exact_face),
        corrected_image=_image(),
        aligned_face=np.zeros((256, 256, 3), dtype=np.uint8),
    )
    accepted_matting = FakeMatting(scale_alpha)
    accepted = _runtime(
        alpha=scale_alpha,
        face_pipeline=FakeFacePipeline(exact_analysis),
        matting=accepted_matting,
    ).generate(_image())

    too_small_face = exact_face.model_copy(
        update={"y2": 180.0 + required_height - 0.01}
    )
    rejected_analysis = FaceQualityAnalysis(
        public=_public_analysis(box=too_small_face),
        corrected_image=_image(),
        aligned_face=np.zeros((256, 256, 3), dtype=np.uint8),
    )
    rejected_matting = FakeMatting(scale_alpha)
    rejected = _runtime(
        alpha=scale_alpha,
        face_pipeline=FakeFacePipeline(rejected_analysis),
        matting=rejected_matting,
    ).generate(_image())

    assert accepted.success is True
    assert accepted.placement is not None
    assert accepted.placement.scale <= 1.5
    assert accepted_matting.calls == 1
    assert rejected.suggestion is Suggestion.RECAPTURE
    assert rejected.reason_codes == (ReasonCode.FACE_TOO_SMALL,)
    assert rejected_matting.calls == 0


def test_template_top_margin_is_checked_after_placement() -> None:
    alpha = _alpha()
    alpha[40:52, 80:400] = 1.0

    result = _runtime(alpha=alpha).generate(_image())

    assert result.reason_codes == (ReasonCode.PORTRAIT_GEOMETRY_INVALID,)
    assert result.suggestion is Suggestion.RECAPTURE


def test_face_analysis_recapture_is_preserved_and_matting_is_skipped() -> None:
    analysis = FaceQualityAnalysis(
        public=_public_analysis(
            suggestion=Suggestion.RECAPTURE,
            reasons=(ReasonCode.NO_FACE,),
            box=None,
            landmarks=None,
        ),
        corrected_image=_image(),
        aligned_face=None,
    )
    matting = FakeMatting()

    result = _runtime(
        face_pipeline=FakeFacePipeline(analysis),
        matting=matting,
    ).generate(_image())

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.NO_FACE,)
    assert result.image is None
    assert matting.calls == 0


def test_oversized_image_is_rejected_before_any_model_inference() -> None:
    storage = np.zeros((1,), dtype=np.uint8)
    oversized = np.lib.stride_tricks.as_strided(
        storage,
        shape=(5_000, 5_001, 3),
        strides=(0, 0, 0),
        writeable=False,
    )
    face_pipeline = FakeFacePipeline()
    matting = FakeMatting()

    result = _runtime(
        face_pipeline=face_pipeline,
        matting=matting,
    ).generate(oversized)

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.IMAGE_TOO_LARGE,)
    assert result.original_width == 5_001
    assert result.original_height == 5_000
    assert face_pipeline.calls == 0
    assert matting.calls == 0


def test_image_at_exact_pixel_limit_reaches_face_quality_inference() -> None:
    storage = np.zeros((1,), dtype=np.uint8)
    boundary = np.lib.stride_tricks.as_strided(
        storage,
        shape=(5_000, 5_000, 3),
        strides=(0, 0, 0),
        writeable=False,
    )
    face_pipeline = FakeFacePipeline()
    matting = FakeMatting()

    result = _runtime(
        face_pipeline=face_pipeline,
        matting=matting,
    ).generate(boundary)

    assert result.success is True
    assert face_pipeline.calls == 1
    assert matting.calls == 1


def test_matting_exception_is_a_hard_review_without_partial_image() -> None:
    result = _runtime(matting=FakeMatting(error=RuntimeError("private detail"))).generate(
        _image()
    )

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.MATTING_FAILED,)
    assert result.image is None
    assert "private detail" not in repr(result)


@pytest.mark.parametrize(
    "alpha",
    [
        np.zeros((10, 10), dtype=np.float32),
        np.full((640, 480), np.nan, dtype=np.float32),
        np.zeros((640, 480), dtype=np.uint8),
    ],
)
def test_malformed_matting_output_maps_to_hard_review(alpha: np.ndarray) -> None:
    result = _runtime(alpha=alpha).generate(_image())

    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.MATTING_FAILED,)
    assert result.image is None


def test_public_result_and_internal_generated_shape_never_have_biometric_fields() -> None:
    result = _runtime().generate(_image())
    names = {field.name.casefold() for field in fields(result)}
    payload = repr(result).casefold() + json.dumps(
        {
            "reason_codes": [reason.value for reason in result.reason_codes],
            "model_versions": dict(result.model_versions),
        },
        sort_keys=True,
    ).casefold()

    assert "embedding" not in names
    assert "feature" not in names
    assert "alpha" not in names
    assert "model" not in names
    assert "session" not in names
    assert "embedding" not in payload
    assert "ndarray" not in repr(result)


def test_exact_alpha_and_shoulder_quality_boundaries_are_allowed() -> None:
    template = _template(
        edge_alpha_threshold=0.50,
        min_shoulder_band_coverage=1.0,
    )

    result = _runtime(template=template).generate(_image())

    assert result.success is True
    assert result.matting_quality is not None
    assert result.matting_quality.shoulder_band_coverage == pytest.approx(1.0)


def test_png_keeps_exact_white_and_jpeg95_keeps_far_background_near_white() -> None:
    result = _runtime().generate(_image())
    assert result.image is not None

    png = cv2.imdecode(
        np.frombuffer(encode_portrait_image(result, output_format="png"), np.uint8),
        cv2.IMREAD_COLOR,
    )
    jpeg = cv2.imdecode(
        np.frombuffer(
            encode_portrait_image(result, output_format="jpeg", jpeg_quality=95),
            np.uint8,
        ),
        cv2.IMREAD_COLOR,
    )

    assert png is not None and jpeg is not None
    assert np.array_equal(png[10, 10], np.asarray([255, 255, 255]))
    assert int(jpeg[0:30, 0:30].min()) >= 250


def test_public_builder_has_only_config_and_registry_dependencies() -> None:
    signature = inspect.signature(build_portrait_generator)

    assert list(signature.parameters) == ["config", "registry"]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    with pytest.raises(TypeError, match="unexpected keyword"):
        build_portrait_generator(
            config=object(),
            registry=object(),
            face_pipeline=object(),
        )


def test_disabled_matting_fails_before_any_face_model_is_loaded(monkeypatch) -> None:
    import face_algorithm.portrait.generator as generator

    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    registry = object.__new__(ModelRegistry)
    face_builder_calls = 0

    def unexpected_face_builder(**_kwargs):
        nonlocal face_builder_calls
        face_builder_calls += 1
        raise AssertionError("face models must not load when matting is disabled")

    monkeypatch.setattr(
        generator,
        "build_owned_face_quality_pipeline",
        unexpected_face_builder,
    )

    with pytest.raises(PortraitMattingDisabledError):
        build_portrait_generator(config=config, registry=registry)

    assert face_builder_calls == 0


def test_runtime_cannot_be_directly_constructed_or_rebound() -> None:
    with pytest.raises(TypeError, match="build_portrait_generator"):
        PortraitGeneratorRuntime()

    runtime = _runtime()
    with pytest.raises((AttributeError, TypeError)):
        runtime._generate = lambda _image: None
