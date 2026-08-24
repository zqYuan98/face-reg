from __future__ import annotations

from dataclasses import fields
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
import psutil
import pytest

from face_algorithm.core.adapters import create_model_registry
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import CardRegion, LandmarkPoint, ReasonCode, Suggestion
from face_algorithm.witness import (
    WITNESS_REQUIRED_MODEL_NAMES,
    build_witness_blur_runtime,
)
from face_algorithm.witness.blur import _blur_witness_pixels
from face_algorithm.witness.card_detector import _detect_card_regions
from face_algorithm.witness.subject_selector import _analyze_witness_subject


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "source"
    / "synthetic_person_holding_blank_card.png"
)
FIXTURE_SHA256 = (
    "70d1518bbb32ef8c38792870a0dbb9e1a05466354d3f5cbfc0cc470e4b0c5773"
)


@pytest.mark.integration
def test_exact_16mp_blur_boundary_keeps_original_size_and_hard_cores() -> None:
    image = np.full((4_000, 4_000, 3), (40, 60, 80), dtype=np.uint8)
    image[200:3_900, 900:3_100] = (30, 100, 180)
    image[2_700:3_501, 1_300:2_701] = (240, 240, 240)
    matte = np.zeros((4_000, 4_000), dtype=np.float32)
    matte[200:3_900, 900:3_100] = 1.0
    card = CardRegion(
        corners=tuple(
            LandmarkPoint(x=float(x), y=float(y))
            for x, y in (
                (1_300, 2_700),
                (2_700, 2_700),
                (2_700, 3_500),
                (1_300, 3_500),
            )
        ),
        confidence=0.90,
    )
    options = load_config(PROJECT_ROOT / "config" / "algorithm.yaml").witness

    output, summary = _blur_witness_pixels(image, matte, (card,), options)

    assert image.shape[0] * image.shape[1] == options.max_witness_pixels
    assert output.shape == image.shape
    assert output.dtype == np.uint8
    assert output.flags.c_contiguous
    np.testing.assert_array_equal(output[1_000, 2_000], image[1_000, 2_000])
    np.testing.assert_array_equal(output[3_000, 2_000], image[3_000, 2_000])
    assert summary.kernel_size <= options.blur_kernel_cap


def _integration_config():
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    models = config.models.model_copy(
        update={
            "portrait_matting": config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    return config.model_copy(update={"models": models})


@pytest.mark.integration
def test_real_models_blur_visible_background_and_keep_the_held_card() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    image = cv2.imdecode(np.frombuffer(FIXTURE.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    original = image.copy()
    config = _integration_config()
    registry = create_model_registry(PROJECT_ROOT / "models.manifest.json", config)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()

    runtime = build_witness_blur_runtime(config=config, registry=registry)
    result = runtime.blur(image)
    probe = runtime._probe_face(image, config.quality_profiles.witness)
    assert probe.reason_code is None and probe.face is not None
    matte = runtime._matte(probe.corrected_image)
    subject = _analyze_witness_subject(matte, probe.face, config.witness).matte
    detection = _detect_card_regions(
        probe.corrected_image,
        probe.face,
        subject,
        config.witness,
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    rss_delta_mb = (process.memory_info().rss - rss_before) / (1024 * 1024)
    print(
        json.dumps(
            {
                "elapsed_ms_including_load_and_two_inferences": elapsed_ms,
                "rss_delta_mb": rss_delta_mb,
                "runtime_suggestion": result.suggestion.value,
                "runtime_reasons": [reason.value for reason in result.reason_codes],
                "trusted_card_count": len(detection.cards),
                "candidate_count": detection.candidate_count,
            },
            sort_keys=True,
        )
    )

    # Every visible subject pixel remains protected through the image edge;
    # the witness face probe separately enforces primary-face geometry.
    assert result.success is True
    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.image is not None
    assert result.image.shape == image.shape
    assert result.image.dtype == np.uint8
    assert result.image.flags.c_contiguous
    assert len(result.card_regions) == 1
    assert detection.reason_code is None
    assert len(detection.cards) == 1
    center_x = sum(point.x for point in detection.cards[0].corners) / 4.0
    center_y = sum(point.y for point in detection.cards[0].corners) / 4.0
    assert 450.0 <= center_x <= 570.0
    assert 830.0 <= center_y <= 950.0
    assert np.array_equal(image, original)
    card_x, card_y = int(round(center_x)), int(round(center_y))
    np.testing.assert_array_equal(result.image[card_y, card_x], image[card_y, card_x])
    face_x = int(round((probe.face.x1 + probe.face.x2) * 0.5))
    face_y = int(round((probe.face.y1 + probe.face.y2) * 0.5))
    np.testing.assert_array_equal(result.image[face_y, face_x], image[face_y, face_x])
    background_slice = np.s_[40:300, 40:340]
    before_frequency = float(
        cv2.Laplacian(image[background_slice], cv2.CV_32F).var()
    )
    after_frequency = float(
        cv2.Laplacian(result.image[background_slice], cv2.CV_32F).var()
    )
    assert after_frequency < before_frequency * 0.75

    assert set(runtime.model_versions) == set(WITNESS_REQUIRED_MODEL_NAMES)
    assert registry._slots["face_recognizer"].state == "idle"
    assert registry._slots["pose_var"].state == "idle"
    assert registry._slots["pose_conv"].state == "idle"
    owned_probe = runtime._probe_face.__self__
    owned_matting = runtime._matte.__self__
    assert owned_probe.detector.detector.ort_session is not registry.get("face_detector")
    assert owned_matting is not registry.get("portrait_matting")
    names = {field.name.casefold() for field in fields(result)}
    assert not ({"embedding", "feature", "alpha", "model", "session"} & names)
