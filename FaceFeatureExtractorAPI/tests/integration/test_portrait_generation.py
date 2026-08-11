from __future__ import annotations

import hashlib
import json
import time
from dataclasses import fields
from pathlib import Path

import cv2
import numpy as np
import psutil
import pytest

from face_algorithm.core.adapters import create_model_registry
from face_algorithm.portrait import (
    build_portrait_generator,
    encode_portrait_image,
)
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import Suggestion


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


def _integration_config():
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    models = config.models.model_copy(
        update={
            "portrait_matting": config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    portrait = config.quality_profiles.portrait
    portrait = portrait.model_copy(
        update={
            "brightness": portrait.brightness.model_copy(
                update={"v0": 0.0, "v1": 1.0, "v2": 254.0, "v3": 255.0}
            ),
            "clarity": portrait.clarity.model_copy(
                update={"low_threshold": 0.001, "high_threshold": 0.002}
            ),
            "pose": portrait.pose.model_copy(
                update={
                    "yaw_threshold": 90.0,
                    "pitch_threshold": 90.0,
                    "roll_threshold": 180.0,
                }
            ),
        }
    )
    profiles = config.quality_profiles.model_copy(update={"portrait": portrait})
    return config.model_copy(
        update={"models": models, "quality_profiles": profiles}
    )


@pytest.mark.integration
def test_real_face_and_modnet_generate_private_standard_portrait() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    image = cv2.imdecode(np.frombuffer(FIXTURE.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    original = image.copy()
    config = _integration_config()
    registry = create_model_registry(PROJECT_ROOT / "models.manifest.json", config)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()

    runtime = build_portrait_generator(config=config, registry=registry)
    result = runtime.generate(image)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    rss_delta_mb = (process.memory_info().rss - rss_before) / (1024 * 1024)
    print(
        json.dumps(
            {
                "elapsed_ms_including_load": elapsed_ms,
                "rss_delta_mb": rss_delta_mb,
                "suggestion": result.suggestion.value,
                "reason_codes": [reason.value for reason in result.reason_codes],
            },
            sort_keys=True,
        )
    )

    assert result.suggestion is Suggestion.PASS, result
    assert result.success is True
    assert result.image is not None
    assert result.image.shape == (640, 480, 3)
    assert result.image.dtype == np.uint8
    assert result.image.flags.c_contiguous
    assert np.array_equal(image, original)
    assert np.array_equal(result.image[0, 0], np.asarray([255, 255, 255]))
    assert float(np.mean(np.all(result.image >= 250, axis=2))) >= 0.20
    assert result.placement is not None
    assert result.placement.scale <= config.portrait_template.max_upscale_ratio
    assert result.matting_quality is not None
    assert (
        result.matting_quality.face_mean
        >= config.portrait_template.min_face_alpha_mean
    )

    # The portrait execution graph owns fresh instances, while the recognizer
    # is not loaded at all for this non-identification task.
    owned_pipeline = runtime._analyze_quality.__self__
    owned_matting = runtime._matte.__self__
    assert registry._slots["face_recognizer"].state == "idle"
    assert "face_recognizer" not in runtime.model_versions
    assert owned_pipeline.detector.detector.ort_session is not registry.get("face_detector")
    assert owned_matting is not registry.get("portrait_matting")

    field_names = {field.name.casefold() for field in fields(result)}
    assert "embedding" not in field_names
    assert "alpha" not in field_names
    assert "session" not in field_names

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
    assert np.array_equal(png[0, 0], np.asarray([255, 255, 255]))
    assert int(jpeg[0:20, 0:20].min()) >= 250
