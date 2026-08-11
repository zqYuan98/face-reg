from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import yaml

from face_algorithm.runtime.config import AlgorithmConfig, load_config
from face_algorithm.portrait.contracts import (
    PortraitMattingQuality,
    PortraitPlacement,
    PortraitResult,
)
from face_algorithm.witness import (
    WitnessBlurResult,
    WitnessBlurSummary,
    WitnessMattingQuality,
)
from face_algorithm.schemas import (
    CardRegion,
    CalibrationSummary,
    FeatureVersion,
    FrameSummary,
    ImageCalibrationSummary,
    ImageVerificationResult,
    ImageVerificationSide,
    LandmarkPoint,
    ReasonCode,
    Suggestion,
    VideoVerificationResult,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def encode_jpeg(*, width: int = 32, height: int = 24) -> bytes:
    image = np.full((height, width, 3), 127, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return bytes(encoded)


def encode_png(*, width: int = 32, height: int = 24) -> bytes:
    image = np.full((height, width, 3), 127, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return bytes(encoded)


def config_with(
    config: AlgorithmConfig,
    *,
    max_image_bytes: int | None = None,
    max_image_pixels: int | None = None,
    max_video_bytes: int | None = None,
    image_deadline: float | None = None,
    quick_deadline: float | None = None,
    full_deadline: float | None = None,
    extract_enabled: bool | None = None,
    detect_enabled: bool | None = None,
    token: str | None = None,
) -> AlgorithmConfig:
    resources = config.resources.model_copy(
        update={
            key: value
            for key, value in {
                "max_image_bytes": max_image_bytes,
                "max_image_pixels": max_image_pixels,
                "max_video_bytes": max_video_bytes,
            }.items()
            if value is not None
        }
    )
    deadlines = config.route_deadlines.model_copy(
        update={
            key: value
            for key, value in {
                "image_seconds": image_deadline,
                "video_quick_seconds": quick_deadline,
                "video_full_seconds": full_deadline,
            }.items()
            if value is not None
        }
    )
    legacy_payload: dict[str, Any] = {}
    if extract_enabled is not None:
        legacy_payload["extract_feature_enabled"] = extract_enabled
    if detect_enabled is not None:
        legacy_payload["detect_face_enabled"] = detect_enabled
    if token is not None:
        from pydantic import SecretStr

        legacy_payload["token"] = SecretStr(token)
    legacy = config.legacy.model_copy(update=legacy_payload)
    return config.model_copy(
        update={
            "resources": resources,
            "route_deadlines": deadlines,
            "legacy": legacy,
        }
    )


@pytest.fixture
def algorithm_config() -> AlgorithmConfig:
    return load_config(PROJECT_ROOT / "config" / "algorithm.yaml")


def portrait_result(image: np.ndarray) -> PortraitResult:
    height, width = image.shape[:2]
    return PortraitResult(
        success=True,
        suggestion=Suggestion.PASS,
        reason_codes=(),
        model_versions={"portrait_matting": "test-v1"},
        config_version="test-config-v1",
        processing_time_ms=1.25,
        trace_id="safe-trace",
        template_version="standard_white_v1",
        output_width=width,
        output_height=height,
        output_format="jpeg",
        jpeg_quality=95,
        original_width=32,
        original_height=24,
        rotation_quarter_turns=0,
        placement=PortraitPlacement(
            scale=1.0,
            translate_x=0.0,
            translate_y=0.0,
            affine=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ),
        matting_quality=PortraitMattingQuality(
            face_mean=1.0,
            face_coverage=1.0,
            subject_area_ratio=0.5,
            shoulder_depth_ratio=0.5,
            shoulder_band_coverage=0.8,
        ),
        image=np.ascontiguousarray(image),
    )


def witness_result(image: np.ndarray) -> WitnessBlurResult:
    height, width = image.shape[:2]
    return WitnessBlurResult(
        success=True,
        suggestion=Suggestion.PASS,
        reason_codes=(),
        model_versions={"portrait_matting": "test-v1"},
        config_version="test-config-v1",
        processing_time_ms=2.5,
        trace_id="safe-witness-trace",
        original_width=width,
        original_height=height,
        rotation_quarter_turns=0,
        coordinate_space="original_image",
        card_regions=(
            CardRegion(
                corners=(
                    LandmarkPoint(x=2.0, y=3.0),
                    LandmarkPoint(x=8.0, y=3.0),
                    LandmarkPoint(x=8.0, y=7.0),
                    LandmarkPoint(x=2.0, y=7.0),
                ),
                confidence=0.9,
            ),
        ),
        card_candidate_count=2,
        protected_area_ratio=0.6,
        matting_quality=WitnessMattingQuality(
            face_mean=0.95,
            face_coverage=0.98,
            subject_area_ratio=0.5,
        ),
        blur_summary=WitnessBlurSummary(
            protected_area_ratio=0.6,
            sigma_pixels=12.0,
            kernel_size=49,
        ),
        image=np.ascontiguousarray(image),
    )


def image_verification_result() -> ImageVerificationResult:
    def side(role: str, trace_id: str) -> ImageVerificationSide:
        return ImageVerificationSide(
            role=role,
            analyzed=True,
            usable=True,
            reason_codes=(),
            original_width=32,
            original_height=24,
            rotation_quarter_turns=0,
            processing_time_ms=1.0,
            trace_id=trace_id,
        )

    return ImageVerificationResult(
        success=True,
        suggestion=Suggestion.REVIEW,
        reason_codes=(ReasonCode.THRESHOLD_UNCALIBRATED,),
        model_versions={"face_recognizer": "test-v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="test-config-v1",
        processing_time_ms=2.0,
        trace_id="verify-trace",
        reference=side("reference", "reference-trace"),
        probe=side("probe", "probe-trace"),
        similarity_score=0.77,
        calibration=ImageCalibrationSummary(status="UNCALIBRATED"),
    )


def video_verification_result(mode: str) -> VideoVerificationResult:
    return VideoVerificationResult(
        success=True,
        suggestion=Suggestion.REVIEW,
        reason_codes=(ReasonCode.THRESHOLD_UNCALIBRATED,),
        model_versions={"face_recognizer": "test-v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="test-config-v1",
        processing_time_ms=3.0,
        trace_id="video-trace",
        mode=mode,
        duration_seconds=1.0,
        frames=FrameSummary(
            requested_frames=4,
            decoded_frames=4,
            analyzed_frames=4,
            valid_frames=4,
            no_face_frames=0,
            multiple_face_frames=0,
            bad_quality_frames=0,
            failed_frames=0,
            first_timestamp_seconds=0.0,
            last_timestamp_seconds=1.0,
        ),
        aggregate_score=0.77,
        median_score=0.78,
        minimum_score=0.70,
        pass_ratio=None,
        primary_track_coverage=1.0,
        secondary_track_ratio=0.0,
        longest_no_face_gap_seconds=0.0,
        calibration=CalibrationSummary(
            status="UNCALIBRATED",
            pipeline_fingerprint="a" * 64,
            policy_fingerprint="b" * 64,
        ),
        liveness_evaluated=False,
    )


class FakeServices:
    def __init__(self) -> None:
        self.started = 0
        self.closed = 0
        self.video_paths: list[Path] = []
        self.deadlines: list[tuple[str, float]] = []

    def start(self) -> None:
        self.started += 1

    def close(self) -> None:
        self.closed += 1

    def readiness(self) -> dict[str, Any]:
        return {
            "status": "degraded",
            "ready": True,
            "models": [],
            "capabilities": {
                "portrait": "ready",
                "image_verification": "ready",
                "video_verification": "ready",
                "witness_blur": "not_installed",
            },
        }

    def generate_portrait(self, image: np.ndarray, *, deadline_monotonic: float):
        assert image.shape[:2] == (24, 32)
        self.deadlines.append(("portrait", deadline_monotonic))
        return portrait_result(np.full((12, 10, 3), 255, dtype=np.uint8))

    def verify_images(
        self,
        reference_image: np.ndarray,
        probe_image: np.ndarray,
        *,
        deadline_monotonic: float,
    ) -> dict[str, Any]:
        assert reference_image.shape[:2] == (24, 32)
        assert probe_image.shape[:2] == (24, 32)
        self.deadlines.append(("images", deadline_monotonic))
        return image_verification_result()

    def verify_video(
        self,
        reference_image: np.ndarray,
        video_path: Path,
        *,
        mode: str,
        deadline_monotonic: float,
    ) -> dict[str, Any]:
        assert reference_image.shape[:2] == (24, 32)
        assert video_path.is_file()
        assert video_path.read_bytes() == b"fake-video"
        self.video_paths.append(video_path)
        self.deadlines.append((mode, deadline_monotonic))
        return video_verification_result(mode)

    def detect_faces(self, image: np.ndarray):
        assert image.shape[:2] == (24, 32)
        return [type("Box", (), {"x1": -1, "y1": 2, "x2": 20, "y2": 30, "score": 0.9})()]

    def extract_feature(self, image: np.ndarray):
        assert image.shape[:2] == (24, 32)
        return np.linspace(0.0, 1.0, 1024, dtype=np.float32)


@pytest.fixture
def fake_services() -> FakeServices:
    return FakeServices()
