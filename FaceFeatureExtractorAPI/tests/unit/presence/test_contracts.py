import json

import pytest
from pydantic import ValidationError

from face_algorithm.schemas import FeatureVersion, ReasonCode, Suggestion


def result_values(**overrides):
    values = dict(
        success=True, suggestion=Suggestion.REVIEW,
        reason_codes=(ReasonCode.CALIBRATION_REQUIRED, ReasonCode.THRESHOLD_UNCALIBRATED),
        model_versions={"face_recognizer": "v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="test", processing_time_ms=0.0, trace_id="trace",
        scan_complete=True, sampling_interval_seconds=0.5,
        sampled_frames=1, decoded_frames=1, valid_face_frames=0, candidate_count=0,
        candidate_limit_hit=False, duration_seconds=0.5, timestamp_source="pts",
    )
    values.update(overrides)
    return values


def test_policy_defaults_are_strict_frozen_and_uncalibrated():
    from face_algorithm.presence.config import VideoPresencePolicy
    policy = VideoPresencePolicy()
    assert policy.model_dump() == dict(
        mode="uncalibrated", sample_interval_seconds=0.5, max_analyzed_frames=1200,
        max_face_candidates=5, max_evidence_frames=5,
        reference_min_face_size=112, video_min_face_size=64,
    )
    with pytest.raises(ValidationError):
        policy.mode = "artifact"


@pytest.mark.parametrize("changes", [
    {"mode": "artifact"}, {"threshold": 0.5}, {"sample_interval_seconds": float("nan")},
    {"sample_interval_seconds": float("inf")}, {"sample_interval_seconds": True},
    {"sample_interval_seconds": 0.0}, {"max_analyzed_frames": 0},
    {"max_analyzed_frames": "1200"}, {"max_face_candidates": 6},
    {"max_evidence_frames": 6}, {"reference_min_face_size": True}, {"video_min_face_size": -1},
])
def test_policy_rejects_unsafe_values(changes):
    from face_algorithm.presence.config import VideoPresencePolicy
    with pytest.raises(ValidationError):
        VideoPresencePolicy(**changes)


def test_result_is_privacy_safe_and_cannot_claim_identity():
    from face_algorithm.presence.contracts import VideoPresenceResult
    result = VideoPresenceResult(**result_values())
    assert result.decision.value == "INCONCLUSIVE"
    assert result.found is None
    assert result.calibration_status == "UNCALIBRATED"
    assert result.mode == "presence"
    assert result.feature_dimension == 1024
    assert result.matcher_version == "l2_dot_v1"
    assert result.liveness_evaluated is False
    schema = json.dumps(VideoPresenceResult.model_json_schema()).lower()
    assert "embedding" not in schema
    assert "landmarks" not in schema


@pytest.mark.parametrize("changes", [
    {"suggestion": Suggestion.PASS}, {"found": True}, {"found": False},
    {"calibration_status": "CALIBRATED"}, {"liveness_evaluated": True},
    {"best_score": 1.0}, {"best_timestamp_seconds": 0.0},
    {"best_score": float("nan"), "best_timestamp_seconds": 0.0},
    {"best_score": 1.1, "best_timestamp_seconds": 0.0},
    {"decoded_frames": 0}, {"valid_face_frames": 2}, {"candidate_count": 6},
    {"sampled_frames": True}, {"duration_seconds": float("nan")},
    {"success": False, "suggestion": Suggestion.RECAPTURE},
    {"reason_codes": ()}, {"embedding": [1.0]},
])
def test_result_rejects_unsafe_claims_and_counts(changes):
    from face_algorithm.presence.contracts import VideoPresenceResult
    with pytest.raises(ValidationError):
        VideoPresenceResult(**result_values(**changes))


def test_evidence_is_typed_unique_bounded_and_consistent():
    from face_algorithm.presence.contracts import PresenceDecision, PresenceEvidenceFrame, VideoPresenceResult
    evidence = PresenceEvidenceFrame(frame_index=0, timestamp_seconds=0.0, score=1.0)
    values = result_values(valid_face_frames=1, candidate_count=1,
                           evidence_frames=(evidence,), best_score=1.0, best_timestamp_seconds=0.0)
    result = VideoPresenceResult(**values)
    assert result.suggestion is Suggestion.REVIEW
    assert result.evidence_frames == (evidence,)
    for changes in (
        {"evidence_frames": (evidence, evidence)}, {"best_score": 0.9},
        {"best_timestamp_seconds": 0.1}, {"valid_face_frames": 0},
        {"decision": PresenceDecision.FOUND}, {"decision": PresenceDecision.NOT_FOUND_IN_SCAN},
        {"evidence_frames": (evidence,) * 6},
        {"evidence_frames": ({"frame_index": 0, "timestamp_seconds": 0.0, "score": 1.0, "photo": "x"},)},
    ):
        with pytest.raises(ValidationError):
            VideoPresenceResult(**(values | changes))


def test_scan_complete_requires_actual_samples_and_failed_scans_cannot_be_complete():
    from face_algorithm.presence.contracts import VideoPresenceResult
    for changes in ({"sampled_frames": 0}, {"success": False}):
        with pytest.raises(ValidationError):
            VideoPresenceResult(**result_values(**changes))


@pytest.mark.parametrize("changes", [{"liveness_evaluated": 0}, {"feature_dimension": 1024.0},
                                    {"candidate_limit_hit": True}])
def test_result_discriminants_and_limit_reason_are_strict(changes):
    from face_algorithm.presence.contracts import VideoPresenceResult
    with pytest.raises(ValidationError):
        VideoPresenceResult(**result_values(**changes))
