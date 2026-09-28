import json
import math

import pytest
from pydantic import ValidationError

from face_algorithm.schemas import (
    FEATURE_DIMENSION,
    IMAGE_RECAPTURE_REASON_CODES,
    VIDEO_RECAPTURE_REASON_CODES,
    CalibrationSummary,
    CardRegion,
    FaceBox,
    FaceLandmarks,
    FeatureVersion,
    FrameSummary,
    HealthLevel,
    LandmarkPoint,
    ModelHealth,
    ModelState,
    Pose,
    QualityMetric,
    QualityResult,
    ReasonCode,
    ResultBase,
    Suggestion,
    VideoVerificationResult,
)


EXPECTED_REASON_CODES = [
    "INVALID_IMAGE",
    "IMAGE_TOO_LARGE",
    "UNSUPPORTED_FORMAT",
    "NO_FACE",
    "MULTIPLE_FACES",
    "FACE_TOO_SMALL",
    "FACE_CROPPED",
    "TOO_DARK",
    "TOO_BRIGHT",
    "TOO_BLURRY",
    "BAD_POSE",
    "LANDMARK_FAILED",
    "ALIGNMENT_FAILED",
    "FEATURE_FAILED",
    "FEATURE_VERSION_MISMATCH",
    "THRESHOLD_UNCALIBRATED",
    "MATTING_FAILED",
    "MATTING_LOW_CONFIDENCE",
    "SUBJECT_CROPPED",
    "SHOULDERS_NOT_VISIBLE",
    "PORTRAIT_GEOMETRY_INVALID",
    "CARD_NOT_FOUND",
    "CARD_TOO_SMALL",
    "CARD_CROPPED",
    "CARD_DETECTION_UNCERTAIN",
    "INVALID_VIDEO",
    "VIDEO_TOO_LARGE",
    "VIDEO_TOO_LONG",
    "VIDEO_DECODE_FAILED",
    "VIDEO_SCAN_LIMIT_REACHED",
    "CANDIDATE_LIMIT_REACHED",
    "INSUFFICIENT_VALID_FRAMES",
    "MULTIPLE_SUBJECTS",
    "SUBJECT_SWITCH",
    "SUBJECT_SWITCHED",
    "CALIBRATION_REQUIRED",
    "IDENTITY_UNCERTAIN",
    "MODEL_UNAVAILABLE",
    "INFERENCE_ERROR",
    "ALGORITHM_TIMEOUT",
]


def _result() -> ResultBase:
    return ResultBase(
        success=False,
        suggestion=Suggestion.RECAPTURE,
        reason_codes=(ReasonCode.NO_FACE, ReasonCode.TOO_BLURRY),
        model_versions={"face_detector": "legacy-v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="2026-08-04-v1",
        processing_time_ms=12.5,
        trace_id="trace-001",
    )


def test_suggestion_has_only_the_three_non_reject_outcomes() -> None:
    assert [item.value for item in Suggestion] == [
        "PASS",
        "RECAPTURE",
        "REVIEW",
    ]


def test_image_recapture_reason_set_is_one_frozen_explicit_contract() -> None:
    assert isinstance(IMAGE_RECAPTURE_REASON_CODES, frozenset)
    assert IMAGE_RECAPTURE_REASON_CODES == {
        ReasonCode.INVALID_IMAGE,
        ReasonCode.IMAGE_TOO_LARGE,
        ReasonCode.UNSUPPORTED_FORMAT,
        ReasonCode.NO_FACE,
        ReasonCode.MULTIPLE_FACES,
        ReasonCode.FACE_TOO_SMALL,
        ReasonCode.FACE_CROPPED,
        ReasonCode.TOO_DARK,
        ReasonCode.TOO_BRIGHT,
        ReasonCode.TOO_BLURRY,
        ReasonCode.BAD_POSE,
        ReasonCode.LANDMARK_FAILED,
        ReasonCode.ALIGNMENT_FAILED,
        ReasonCode.MATTING_LOW_CONFIDENCE,
        ReasonCode.SUBJECT_CROPPED,
        ReasonCode.SHOULDERS_NOT_VISIBLE,
        ReasonCode.PORTRAIT_GEOMETRY_INVALID,
    }
    assert ReasonCode.FEATURE_FAILED not in IMAGE_RECAPTURE_REASON_CODES
    assert ReasonCode.THRESHOLD_UNCALIBRATED not in IMAGE_RECAPTURE_REASON_CODES
    assert tuple(Suggestion) == (
        Suggestion.PASS,
        Suggestion.RECAPTURE,
        Suggestion.REVIEW,
    )


def test_reason_codes_are_complete_and_serialize_exactly() -> None:
    assert [item.value for item in ReasonCode] == EXPECTED_REASON_CODES
    serialized = json.loads(_result().model_dump_json())
    assert serialized["reason_codes"] == ["NO_FACE", "TOO_BLURRY"]


def test_feature_contract_has_one_version_and_dimension_1024() -> None:
    assert [item.value for item in FeatureVersion] == ["recognizer_v1_1024"]
    assert FEATURE_DIMENSION == 1024


def test_public_schemas_are_strict_frozen_and_never_expose_embeddings() -> None:
    result = _result()
    with pytest.raises(ValidationError):
        result.success = True
    with pytest.raises(TypeError):
        result.model_versions["face_detector"] = "mutated"
    with pytest.raises(ValidationError):
        FaceBox(x1=0.0, y1=0.0, x2=10.0, y2=10.0, surprise=True)

    payload = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert "embedding" not in payload.casefold()
    assert "feature_vector" not in payload.casefold()


def test_geometry_quality_and_health_types_validate_structured_values() -> None:
    box = FaceBox(x1=10.0, y1=20.0, x2=110.0, y2=220.0, confidence=0.95)
    landmarks = FaceLandmarks(
        points=tuple(
            LandmarkPoint(x=float(index), y=float(index + 1))
            for index in range(5)
        )
    )
    pose = Pose(yaw=1.0, pitch=-2.0, roll=3.0)
    metric = QualityMetric(name="blur", value=0.25, passed=True)
    quality = QualityResult(passed=True, metrics=(metric,), reason_codes=())
    card = CardRegion(
        corners=(
            LandmarkPoint(x=1.0, y=1.0),
            LandmarkPoint(x=5.0, y=1.0),
            LandmarkPoint(x=5.0, y=3.0),
            LandmarkPoint(x=1.0, y=3.0),
        ),
        confidence=0.8,
    )
    model = ModelHealth(
        name="face_recognizer",
        required=True,
        state=ModelState.READY,
        version="recognizer-v1",
        providers=("CPUExecutionProvider",),
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        output_dimension=1024,
    )

    assert box.width == 100.0 and box.height == 200.0
    assert len(landmarks.points) == 5
    assert math.isfinite(pose.pitch)
    assert quality.metrics[0].passed
    assert len(card.corners) == 4
    assert model.state is ModelState.READY
    assert HealthLevel.HEALTHY.value == "healthy"


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (FaceBox, {"x1": 1.0, "y1": 0.0, "x2": 1.0, "y2": 5.0}),
        (LandmarkPoint, {"x": math.inf, "y": 0.0}),
        (Pose, {"yaw": math.nan, "pitch": 0.0, "roll": 0.0}),
    ],
)
def test_geometry_rejects_degenerate_or_non_finite_values(factory, kwargs) -> None:
    with pytest.raises(ValidationError):
        factory(**kwargs)


def _frame_summary() -> FrameSummary:
    return FrameSummary(
        requested_frames=12,
        decoded_frames=12,
        analyzed_frames=12,
        valid_frames=12,
        no_face_frames=0,
        multiple_face_frames=0,
        bad_quality_frames=0,
        failed_frames=0,
        first_timestamp_seconds=0.0,
        last_timestamp_seconds=5.5,
        reason_codes=(),
    )


def _video_result(*, active: bool) -> VideoVerificationResult:
    calibration = CalibrationSummary(
        status="ACTIVE" if active else "UNCALIBRATED",
        calibration_id="video-cal-v1" if active else None,
        artifact_sha256="c" * 64 if active else None,
        frame_threshold=0.72 if active else None,
        aggregate_threshold=0.75 if active else None,
        min_pass_ratio=0.70 if active else None,
        decision_rule_version="video_track_aggregate_v1" if active else None,
        pipeline_fingerprint="a" * 64,
        policy_fingerprint="b" * 64,
    )
    return VideoVerificationResult(
        success=True,
        suggestion=Suggestion.PASS if active else Suggestion.REVIEW,
        reason_codes=(
            ()
            if active
            else (
                ReasonCode.CALIBRATION_REQUIRED,
                ReasonCode.THRESHOLD_UNCALIBRATED,
            )
        ),
        model_versions={"face_recognizer": "v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="config-v1",
        processing_time_ms=10.0,
        trace_id="video-trace",
        mode="quick",
        duration_seconds=6.0,
        frames=_frame_summary(),
        aggregate_score=1.0,
        median_score=1.0,
        minimum_score=1.0,
        pass_ratio=1.0 if active else None,
        primary_track_coverage=1.0,
        secondary_track_ratio=0.0,
        longest_no_face_gap_seconds=0.0,
        feature_dimension=1024,
        matcher_version="l2_dot_v1",
        calibration=calibration,
        liveness_evaluated=False,
    )


def test_video_public_contract_contains_only_safe_aggregates() -> None:
    result = _video_result(active=False)
    payload = result.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True)

    assert result.suggestion is Suggestion.REVIEW
    assert "embedding" not in encoded.casefold()
    assert "landmark" not in encoded.casefold()
    assert "image" not in encoded.casefold()
    assert "session" not in encoded.casefold()
    assert "reference" not in encoded.casefold()
    assert result.liveness_evaluated is False


def test_uncalibrated_video_result_can_never_pass() -> None:
    result = _video_result(active=False)

    with pytest.raises(ValidationError, match="uncalibrated"):
        VideoVerificationResult.model_validate(
            {**result.model_dump(mode="python"), "suggestion": Suggestion.PASS}
        )


def test_active_video_pass_must_meet_both_signed_thresholds() -> None:
    result = _video_result(active=True)

    with pytest.raises(ValidationError, match="threshold"):
        VideoVerificationResult.model_validate(
            {**result.model_dump(mode="python"), "aggregate_score": 0.74}
        )
    with pytest.raises(ValidationError, match="threshold"):
        VideoVerificationResult.model_validate(
            {**result.model_dump(mode="python"), "pass_ratio": 0.69}
        )


def test_video_recapture_rejects_identity_decision_reasons() -> None:
    result = _video_result(active=True)

    assert ReasonCode.INSUFFICIENT_VALID_FRAMES in VIDEO_RECAPTURE_REASON_CODES
    assert ReasonCode.IDENTITY_UNCERTAIN not in VIDEO_RECAPTURE_REASON_CODES
    with pytest.raises(ValidationError, match="RECAPTURE"):
        VideoVerificationResult.model_validate(
            {
                **result.model_dump(mode="python"),
                "suggestion": Suggestion.RECAPTURE,
                "reason_codes": (ReasonCode.IDENTITY_UNCERTAIN,),
            }
        )


def test_active_video_identity_review_must_fail_a_signed_threshold() -> None:
    result = _video_result(active=True)
    payload = {
        **result.model_dump(mode="python"),
        "suggestion": Suggestion.REVIEW,
        "reason_codes": (ReasonCode.IDENTITY_UNCERTAIN,),
    }

    with pytest.raises(ValidationError, match="uncertain REVIEW"):
        VideoVerificationResult.model_validate(payload)

    uncertain = VideoVerificationResult.model_validate(
        {**payload, "aggregate_score": 0.74}
    )
    assert uncertain.suggestion is Suggestion.REVIEW
