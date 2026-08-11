from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest

from face_algorithm.core.adapters import create_model_registry
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import ReasonCode, Suggestion
from face_algorithm.video.verifier import build_video_verification_runtime
from scripts.generate_fixture_video import generate_fixture_video


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT_ROOT / "tests" / "fixtures" / "source" / "synthetic_person_holding_blank_card.png"
SOURCE_SHA256 = "70d1518bbb32ef8c38792870a0dbb9e1a05466354d3f5cbfc0cc470e4b0c5773"


@pytest.mark.integration
def test_real_synthetic_video_quick_and_full_are_bounded_and_uncalibrated(
    tmp_path: Path,
) -> None:
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA256
    image = cv2.imdecode(
        np.frombuffer(SOURCE.read_bytes(), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert image is not None
    video_path = tmp_path / "same-subject.mp4"
    generated = generate_fixture_video(
        input_path=SOURCE,
        output_path=video_path,
        fps=4.0,
        duration_seconds=10.0,
    )
    assert generated.frame_count == 40

    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    registry = create_model_registry(PROJECT_ROOT / "models.manifest.json", config)
    runtime = build_video_verification_runtime(
        registry=registry,
        config=config,
        key_store=None,
    )
    caller_session = registry.get("face_recognizer")
    owned_pipeline = runtime._verifier._analyze_trusted.__self__
    owned_session = owned_pipeline.embedder.recognizer.ort_session
    assert owned_session is not caller_session

    had_instance_run = "run" in vars(caller_session)
    original_instance_run = vars(caller_session).get("run")

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("caller-owned recognizer must not serve video requests")

    caller_session.run = forbidden_run
    try:
        quick = runtime.quick_verify(image, video_path)
        full = runtime.full_verify(image, video_path)
        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent_quick = tuple(
                executor.map(
                    lambda _index: runtime.quick_verify(image, video_path),
                    range(2),
                )
            )
    finally:
        if had_instance_run:
            caller_session.run = original_instance_run
        else:
            del caller_session.run

    for result, minimum_valid in (
        (quick, config.video_verification.quick.min_valid_frames),
        (full, config.video_verification.full.min_valid_frames),
    ):
        assert result.success is True, result.model_dump()
        assert result.suggestion is Suggestion.REVIEW, result.model_dump()
        assert result.reason_codes == (
            ReasonCode.CALIBRATION_REQUIRED,
            ReasonCode.THRESHOLD_UNCALIBRATED,
        )
        assert result.frames.valid_frames >= minimum_valid
        assert result.aggregate_score is not None
        assert np.isfinite(result.aggregate_score)
        assert result.pass_ratio is None
        assert result.calibration.status == "UNCALIBRATED"
        assert result.feature_dimension == 1024
        assert result.feature_version.value == "recognizer_v1_1024"
        assert result.liveness_evaluated is False
        assert result.duration_seconds == pytest.approx(10.0, abs=0.26)
    assert quick.mode == "quick"
    assert quick.frames.requested_frames == 12
    assert quick.frames.analyzed_frames == 12
    assert full.mode == "full"
    assert full.frames.requested_frames == 20
    assert full.frames.analyzed_frames == 20
    assert all(item.frames.analyzed_frames == 12 for item in concurrent_quick)
    assert all(item.reason_codes == quick.reason_codes for item in concurrent_quick)
    assert all(
        item.aggregate_score == pytest.approx(quick.aggregate_score, abs=1e-7)
        for item in concurrent_quick
    )
