"""Real portrait orchestration with deterministic model boundary inputs."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from face_algorithm.portrait.generator import _bind_portrait_generator, encode_portrait_image
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import FaceBox, ReasonCode, Suggestion
from tests.unit.core.test_pipeline import (
    FaceAwareLandmarker, FakeDetector, FakeEmbedder, _pipeline, _textured_image,
)
from tests.unit.portrait.test_face_archive import _Matting


def _runtime(*, mode="face_archive", bottom=405, empty_matte=False):
    config = load_config(Path(__file__).resolve().parents[3] / "config/algorithm.yaml")
    config = config.model_copy(update={
        "portrait_template": config.portrait_template.model_copy(update={"mode": mode}),
    })
    image = _textured_image()
    raw = FaceBox(x1=100, y1=190, x2=300, y2=bottom, confidence=.99)
    embedder = FakeEmbedder()
    pipeline = _pipeline(detector=FakeDetector([raw]), landmarker=FaceAwareLandmarker(), embedder=embedder)
    alpha = np.zeros(image.shape[:2], dtype=np.float32)
    if not empty_matte:
        alpha[165:, 100:300] = 1
    matting = _Matting(alpha)
    generator = _bind_portrait_generator(face_pipeline=pipeline, matting_runtime=matting, config=config)
    return generator, image, matting, embedder


def test_real_pipeline_to_jpeg_accepts_small_box_overflow_as_warning():
    generator, image, matting, embedder = _runtime()
    original = image.copy()
    result = generator.generate(image)
    assert result.success, result.reason_codes
    assert result.suggestion is Suggestion.REVIEW
    assert ReasonCode.PORTRAIT_SOURCE_LIMITED in result.reason_codes
    assert ReasonCode.FACE_CROPPED not in result.reason_codes
    assert result.placement and result.matting_quality
    assert 0 < result.placement.scale <= 1.5
    p = result.placement
    assert p.scale * 100 + p.translate_x >= -1e-6
    assert p.scale * 300 + p.translate_x <= 480 + 1e-6
    assert p.scale * 190 + p.translate_y >= -1e-6
    assert p.scale * 400 + p.translate_y <= 640 + 1e-6
    decoded = cv2.imdecode(np.frombuffer(encode_portrait_image(result), np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (640, 480, 3)
    assert np.mean(np.all(decoded >= 250, axis=2)) > .10
    np.testing.assert_array_equal(image, original)
    assert matting.calls == 1 and embedder.calls == 0


@pytest.mark.parametrize("mode,bottom", [("head_shoulders", 405), ("face_archive", 500)])
def test_strict_or_severe_overflow_still_rejects_before_matting(mode, bottom):
    generator, image, matting, embedder = _runtime(mode=mode, bottom=bottom)
    result = generator.generate(image)
    assert not result.success and result.image is None
    assert result.placement is None and result.matting_quality is None
    assert ReasonCode.FACE_CROPPED in result.reason_codes
    assert matting.calls == embedder.calls == 0


def test_mild_overflow_does_not_bypass_matting_failure():
    generator, image, matting, embedder = _runtime(empty_matte=True)
    result = generator.generate(image)
    assert not result.success and result.image is None
    assert result.placement is None and result.matting_quality is None
    assert ReasonCode.MATTING_LOW_CONFIDENCE in result.reason_codes
    assert matting.calls == 1 and embedder.calls == 0
