from __future__ import annotations

from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from face_algorithm.core.pipeline import FaceQualityAnalysis
from face_algorithm.portrait.contracts import (
    PortraitMattingQuality,
    PortraitPlacement,
    PortraitResult,
)
from face_algorithm.portrait.generator import _bind_portrait_generator, encode_portrait_image
from face_algorithm.portrait.subject_validator import validate_portrait_subject
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import (
    FaceAnalysisResult, FaceBox, FaceLandmarks, FeatureVersion, LandmarkPoint,
    ReasonCode, Suggestion,
)


ROOT = Path(__file__).resolve().parents[3]
SOFT_NAMES = ("TOO_DARK", "TOO_BRIGHT", "TOO_BLURRY", "BAD_POSE", "PORTRAIT_SOURCE_LIMITED")
SOFT_COMBINATIONS = [group for count in range(1, 6) for group in combinations(SOFT_NAMES, count)]


def _face(x1=100.0, y1=70.0, width=220.0, height=307.2):
    return FaceBox(x1=x1, y1=y1, x2=x1 + width, y2=y1 + height, confidence=0.99)


def _landmarks(face):
    return FaceLandmarks(points=tuple(
        LandmarkPoint(x=face.x1 + x * face.width, y=face.y1 + y * face.height)
        for x, y in ((0.3, 0.35), (0.7, 0.35), (0.5, 0.55), (0.35, 0.75), (0.65, 0.75))
    ))


def _source(face, shape=(480, 420), *, shoulders=0, cropped_top=False):
    image = np.full((*shape, 3), (40, 100, 180), dtype=np.uint8)
    alpha = np.zeros(shape, dtype=np.float32)
    top = 0 if cropped_top else max(0, int(face.y1) - 25)
    alpha[top:int(np.ceil(face.y2)) + shoulders, int(face.x1):int(np.ceil(face.x2))] = 1
    return image, alpha


def _analysis(image, face, reasons=(), *, suggestion=None, success=True):
    public = FaceAnalysisResult(
        success=success,
        suggestion=suggestion or (Suggestion.RECAPTURE if reasons else Suggestion.PASS),
        reason_codes=reasons, model_versions={"face_detector": "unit"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="unit", processing_time_ms=0.0, trace_id="unit",
        original_width=image.shape[1], original_height=image.shape[0],
        box=face, landmarks=_landmarks(face), quality=None, pose=None,
        rotation_quarter_turns=0, processing_steps=("detection", "landmarks", "quality"),
    )
    return FaceQualityAnalysis(public=public, corrected_image=image, aligned_face=None)


class _FacePipeline:
    model_versions = {"face_detector": "unit"}

    def __init__(self, analysis):
        self.analysis = analysis
        self.archive_calls = 0
        self.strict_calls = 0

    def analyze_portrait_quality(self, image, profile):
        self.archive_calls += 1
        return self.analysis

    def analyze_quality(self, image, profile):
        self.strict_calls += 1
        return self.analysis

    def embed(self, *args):
        raise AssertionError("portrait must not request identity embeddings")


class _Matting:
    model_info = SimpleNamespace(version="unit-matte")

    def __init__(self, alpha):
        self.alpha = alpha
        self.calls = 0

    def matte(self, image):
        self.calls += 1
        return self.alpha.copy()


def _generate(face=None, *, shape=(480, 420), shoulders=0, cropped_top=False,
              reasons=(), alpha=None, mode="face_archive", **template_updates):
    face = face or _face()
    image, default_alpha = _source(face, shape, shoulders=shoulders, cropped_top=cropped_top)
    config = load_config(ROOT / "config/algorithm.yaml")
    template = config.portrait_template.model_copy(update={"mode": mode, **template_updates})
    config = config.model_copy(update={"portrait_template": template})
    pipeline = _FacePipeline(_analysis(image, face, reasons))
    matting = _Matting(default_alpha if alpha is None else alpha)
    runtime = _bind_portrait_generator(face_pipeline=pipeline, matting_runtime=matting, config=config)
    return runtime.generate(image), pipeline, matting


def _assert_full_face(result, face):
    assert result.success is True
    placement = result.placement
    assert placement is not None
    assert 0 < placement.scale <= 1.5
    assert placement.affine[0][0] == placement.affine[1][1] == placement.scale
    assert placement.affine[0][1] == placement.affine[1][0] == 0.0
    assert placement.scale * face.x1 + placement.translate_x >= -1e-6
    assert placement.scale * face.y1 + placement.translate_y >= -1e-6
    assert placement.scale * face.x2 + placement.translate_x <= 480 + 1e-6
    assert placement.scale * face.y2 + placement.translate_y <= 640 + 1e-6


def test_archive_closeup_without_shoulders_generates_pass_and_real_zero_metrics():
    face = _face(height=307.0)
    result, pipeline, _ = _generate(face, shape=(377, 420))

    _assert_full_face(result, face)
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.matting_quality.shoulder_depth_ratio == 0
    assert result.matting_quality.shoulder_band_coverage == 0
    assert pipeline.archive_calls == 1
    assert pipeline.strict_calls == 0
    assert result.image.shape == (640, 480, 3)
    assert np.array_equal(result.image[0, 0], [255, 255, 255])


@pytest.mark.parametrize("edge", ["left", "right", "bottom"])
def test_archive_clothing_touching_edges_needs_no_warning(edge):
    face = _face()
    _, alpha = _source(face)
    if edge == "left":
        alpha[350:, :320] = 1
    elif edge == "right":
        alpha[350:, 100:] = 1
    else:
        alpha[350:, 100:320] = 1
    result, _, _ = _generate(face, alpha=alpha)

    _assert_full_face(result, face)
    assert result.reason_codes == ()


def test_archive_partial_head_top_is_source_warning_not_hard_failure():
    face = _face(y1=4, height=307)
    result, _, _ = _generate(face, cropped_top=True)

    _assert_full_face(result, face)
    assert result.suggestion is Suggestion.REVIEW
    assert [reason.value for reason in result.reason_codes] == ["PORTRAIT_SOURCE_LIMITED"]


@pytest.mark.parametrize("height", [112, 113, 160, 204])
def test_archive_112_to_204_pixel_faces_generate_at_cap_with_white_padding(height):
    face = _face(width=112, height=height)
    result, _, matting = _generate(face, shape=(300, 300))

    _assert_full_face(result, face)
    assert matting.calls == 1
    assert result.placement.scale == pytest.approx(1.5)
    assert result.suggestion is Suggestion.REVIEW
    assert [reason.value for reason in result.reason_codes] == ["PORTRAIT_UPSCALE_LIMITED"]
    assert np.array_equal(result.image[0, 0], [255, 255, 255])


@pytest.mark.parametrize("width,height,shape", [
    (1000, 112, (220, 1200)), (112, 1800, (1900, 240)),
    (1200, 1200, (1300, 1400)), (112, 112, (112, 112)),
])
def test_archive_extreme_face_aspects_remain_complete_without_stretch(width, height, shape):
    face = _face(x1=0, y1=0, width=width, height=height)
    result, _, _ = _generate(face, shape=shape)

    _assert_full_face(result, face)
    expected = min(640 * 0.48 / height, 1.5, 480 / width, 640 / height)
    assert result.placement.scale == pytest.approx(expected)


def test_archive_keeps_visible_head_when_ideal_eye_position_would_crop_it():
    face = _face(x1=100, y1=260, height=220)
    _, alpha = _source(face, (700, 420))
    alpha[50:260, 100:320] = 1
    result, _, _ = _generate(face, shape=(700, 420), alpha=alpha)

    _assert_full_face(result, face)
    assert result.placement.scale * 50 + result.placement.translate_y >= -1e-6


@pytest.mark.parametrize("names", SOFT_COMBINATIONS)
@pytest.mark.parametrize("upscale_limited", [False, True])
def test_every_quality_warning_combination_generates_review_with_image(names, upscale_limited):
    reasons = tuple(ReasonCode[name] for name in names)
    face = _face(width=112, height=160) if upscale_limited else _face()
    result, pipeline, _ = _generate(face, reasons=reasons)

    assert result.success is True
    assert result.suggestion is Suggestion.REVIEW
    expected = reasons + ((ReasonCode.PORTRAIT_UPSCALE_LIMITED,) if upscale_limited else ())
    assert result.reason_codes == expected
    assert result.image is not None and result.placement is not None
    assert result.matting_quality is not None
    assert pipeline.archive_calls == 1 and pipeline.strict_calls == 0
    decoded = cv2.imdecode(np.frombuffer(encode_portrait_image(result), np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (640, 480, 3)


@pytest.mark.parametrize("hard", ["NO_FACE", "MULTIPLE_FACES", "FACE_CROPPED", "LANDMARK_FAILED", "INFERENCE_ERROR"])
def test_archive_never_whitelists_hard_failures_even_with_soft_warning(hard):
    reasons = (ReasonCode.TOO_DARK, ReasonCode[hard])
    result, _, matting = _generate(reasons=reasons)

    assert result.success is False
    assert ReasonCode[hard] in result.reason_codes
    assert result.reason_codes[0] is ReasonCode[hard]
    assert result.image is result.placement is result.matting_quality is None
    assert matting.calls == 0


@pytest.mark.parametrize("kind", ["nan", "negative", "over_one", "shape", "empty", "face_hole"])
def test_archive_bad_matte_is_hard_failure_even_with_quality_warnings(kind):
    face = _face()
    _, alpha = _source(face)
    if kind == "nan":
        alpha[0, 0] = np.nan
    elif kind == "negative":
        alpha[0, 0] = -0.1
    elif kind == "over_one":
        alpha[0, 0] = 1.1
    elif kind == "shape":
        alpha = alpha[:-1]
    elif kind == "empty":
        alpha[:] = 0
    else:
        alpha[100:350, 100:210] = 0
    result, _, _ = _generate(face, alpha=alpha, reasons=(ReasonCode.TOO_DARK,))

    assert result.success is False
    assert result.image is result.placement is result.matting_quality is None
    assert any(code in result.reason_codes for code in (ReasonCode.MATTING_FAILED, ReasonCode.MATTING_LOW_CONFIDENCE))


@pytest.mark.parametrize("dimension", ["width", "height"])
def test_archive_still_rejects_source_face_below_112(dimension):
    face = _face(**{dimension: 111.99})
    result, _, matting = _generate(face)

    assert result.success is False
    assert result.reason_codes == (ReasonCode.FACE_TOO_SMALL,)
    assert matting.calls == 0


def test_archive_subject_metrics_report_actual_partial_shoulder_depth():
    config = load_config(ROOT / "config/algorithm.yaml")
    face = _face(height=200)
    _, alpha = _source(face, shape=(290, 420), shoulders=20)

    validation = validate_portrait_subject(alpha, face, config.portrait_template)

    assert validation.shoulder_depth_ratio == pytest.approx(0.10)
    assert validation.shoulder_band_coverage == pytest.approx(220 / 286)


def test_duplicate_source_warning_is_merged_once():
    result, _, _ = _generate(_face(y1=4), cropped_top=True,
                             reasons=(ReasonCode.PORTRAIT_SOURCE_LIMITED,))

    assert result.success is True
    assert result.reason_codes == (ReasonCode.PORTRAIT_SOURCE_LIMITED,)


def test_upscale_warning_cannot_be_injected_by_quality_stage():
    result, _, matting = _generate(reasons=(ReasonCode.PORTRAIT_UPSCALE_LIMITED,))

    assert result.success is False
    assert ReasonCode.INFERENCE_ERROR in result.reason_codes
    assert result.image is result.placement is result.matting_quality is None
    assert matting.calls == 0


def _result(**updates):
    values = dict(
        success=True, suggestion=Suggestion.PASS, reason_codes=(),
        model_versions={"portrait_matting": "unit"}, config_version="unit",
        processing_time_ms=0.0, trace_id="unit", template_version="face-archive-480x640-v1",
        output_width=480, output_height=640, output_format="jpeg", jpeg_quality=95,
        original_width=420, original_height=480, rotation_quarter_turns=0,
        placement=PortraitPlacement(1.0, 0.0, 0.0, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))),
        matting_quality=PortraitMattingQuality(1.0, 1.0, 0.5, 0.0, 0.0),
        image=np.zeros((640, 480, 3), np.uint8),
    )
    values.update(updates)
    return PortraitResult(**values)


@pytest.mark.parametrize("name", (*SOFT_NAMES, "PORTRAIT_UPSCALE_LIMITED"))
def test_result_allows_whitelisted_review_success(name):
    result = _result(suggestion=Suggestion.REVIEW, reason_codes=(ReasonCode[name],))

    assert result.success is True


@pytest.mark.parametrize("updates", [
    {"suggestion": Suggestion.REVIEW},
    {"suggestion": Suggestion.RECAPTURE, "reason_codes": (ReasonCode.TOO_DARK,)},
    {"reason_codes": (ReasonCode.TOO_DARK,)},
    {"suggestion": Suggestion.REVIEW, "reason_codes": (ReasonCode.FACE_CROPPED,)},
    {"image": None}, {"placement": None}, {"matting_quality": None},
])
def test_result_rejects_inconsistent_success(updates):
    with pytest.raises(ValueError):
        _result(**updates)


@pytest.mark.parametrize("field", ["image", "placement", "matting_quality"])
def test_result_rejects_any_partial_derived_artifact_on_failure(field):
    success = _result()
    updates = dict(success=False, suggestion=Suggestion.REVIEW,
                   reason_codes=(ReasonCode.MATTING_FAILED,), image=None,
                   placement=None, matting_quality=None)
    updates[field] = getattr(success, field)

    with pytest.raises(ValueError):
        _result(**updates)


@pytest.mark.parametrize("name", ["PORTRAIT_SOURCE_LIMITED", "PORTRAIT_UPSCALE_LIMITED"])
def test_result_rejects_failure_with_only_new_archive_warning(name):
    with pytest.raises(ValueError):
        _result(success=False, suggestion=Suggestion.REVIEW, reason_codes=(ReasonCode[name],),
                image=None, placement=None, matting_quality=None)


@pytest.mark.parametrize("name", SOFT_NAMES[:4])
def test_result_old_quality_only_failures_require_strict_recapture_action(name):
    with pytest.raises(ValueError):
        _result(success=False, suggestion=Suggestion.REVIEW,
                reason_codes=(ReasonCode[name],), image=None,
                placement=None, matting_quality=None)


def test_strict_old_quality_recapture_is_still_a_valid_failure():
    result, pipeline, matting = _generate(mode="head_shoulders", reasons=(ReasonCode.TOO_DARK,))

    assert result.success is False
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.TOO_DARK,)
    assert result.image is None
    assert pipeline.strict_calls == 1 and pipeline.archive_calls == 0
    assert matting.calls == 0
