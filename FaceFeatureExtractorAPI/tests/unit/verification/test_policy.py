from __future__ import annotations

import math

import pytest

from face_algorithm.runtime.calibration import CalibrationState, CalibrationStatus
from face_algorithm.schemas import ReasonCode, Suggestion
from face_algorithm.verification.policy import decide_image_verification


_RECAPTURE_REASONS = (
    ReasonCode.INVALID_IMAGE,
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
)
_SYSTEM_REASONS = (
    ReasonCode.FEATURE_FAILED,
    ReasonCode.FEATURE_VERSION_MISMATCH,
    ReasonCode.MODEL_UNAVAILABLE,
    ReasonCode.INFERENCE_ERROR,
    ReasonCode.ALGORITHM_TIMEOUT,
)


def _forge_calibration_state(
    *,
    status: CalibrationStatus,
    calibration_id: str | None,
    threshold: float | None,
    artifact_sha256: str | None,
    artifact: object,
) -> CalibrationState:
    state = object.__new__(CalibrationState)
    object.__setattr__(state, "status", status)
    object.__setattr__(state, "calibration_id", calibration_id)
    object.__setattr__(state, "threshold", threshold)
    object.__setattr__(state, "artifact_sha256", artifact_sha256)
    object.__setattr__(state, "artifact", artifact)
    return state


@pytest.mark.parametrize("reason", _RECAPTURE_REASONS)
def test_correctable_input_or_quality_failure_requests_recapture(
    reason: ReasonCode,
    uncalibrated_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(reason,),
        probe_reason_codes=(),
        score=None,
        calibration=uncalibrated_calibration,
    )

    assert decision.success is True
    assert decision.suggestion is Suggestion.RECAPTURE
    assert decision.reason_codes == (reason,)
    assert decision.decision_threshold is None


@pytest.mark.parametrize("reason", _SYSTEM_REASONS)
def test_system_or_contract_failure_is_unsuccessful_review(
    reason: ReasonCode,
    active_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(),
        probe_reason_codes=(reason,),
        score=None,
        calibration=active_calibration,
    )

    assert decision.success is False
    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (reason,)


def test_uncalibrated_score_of_one_can_never_pass(
    uncalibrated_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(),
        probe_reason_codes=(),
        score=1.0,
        calibration=uncalibrated_calibration,
    )

    assert decision.success is True
    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (ReasonCode.THRESHOLD_UNCALIBRATED,)
    assert decision.decision_threshold is None


@pytest.mark.parametrize(
    ("score", "suggestion", "reasons"),
    [
        (0.75, Suggestion.PASS, ()),
        (0.7500001, Suggestion.PASS, ()),
        (0.7499999, Suggestion.REVIEW, (ReasonCode.IDENTITY_UNCERTAIN,)),
    ],
)
def test_active_signed_threshold_uses_score_gte_rule(
    score: float,
    suggestion: Suggestion,
    reasons: tuple[ReasonCode, ...],
    active_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(),
        probe_reason_codes=(),
        score=score,
        calibration=active_calibration,
    )

    assert decision.success is True
    assert decision.suggestion is suggestion
    assert decision.reason_codes == reasons
    assert decision.decision_threshold == pytest.approx(0.75)


@pytest.mark.parametrize("score", [None, math.nan, math.inf, -1.01, 1.01])
def test_missing_or_invalid_match_score_fails_closed(
    score: float | None,
    active_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(),
        probe_reason_codes=(),
        score=score,
        calibration=active_calibration,
    )

    assert decision.success is False
    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (ReasonCode.FEATURE_FAILED,)


def test_non_image_reason_fails_closed_as_system_review(
    active_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(ReasonCode.CARD_NOT_FOUND,),
        probe_reason_codes=(),
        score=None,
        calibration=active_calibration,
    )

    assert decision.success is False
    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (ReasonCode.CARD_NOT_FOUND,)


def test_system_failure_takes_priority_over_other_side_recapture(
    active_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(ReasonCode.TOO_BLURRY,),
        probe_reason_codes=(ReasonCode.INFERENCE_ERROR,),
        score=None,
        calibration=active_calibration,
    )

    assert decision.success is False
    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (
        ReasonCode.TOO_BLURRY,
        ReasonCode.INFERENCE_ERROR,
    )


def test_two_side_quality_reasons_are_stably_merged_and_deduplicated(
    active_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(ReasonCode.TOO_DARK, ReasonCode.TOO_BLURRY),
        probe_reason_codes=(ReasonCode.TOO_BLURRY, ReasonCode.BAD_POSE),
        score=None,
        calibration=active_calibration,
    )

    assert decision.suggestion is Suggestion.RECAPTURE
    assert decision.reason_codes == (
        ReasonCode.TOO_DARK,
        ReasonCode.TOO_BLURRY,
        ReasonCode.BAD_POSE,
    )


def test_recapture_cannot_pass_even_if_score_and_active_threshold_were_supplied(
    active_calibration: CalibrationState,
) -> None:
    decision = decide_image_verification(
        reference_reason_codes=(ReasonCode.NO_FACE,),
        probe_reason_codes=(),
        score=1.0,
        calibration=active_calibration,
    )

    assert decision.success is True
    assert decision.suggestion is Suggestion.RECAPTURE
    assert decision.reason_codes == (ReasonCode.NO_FACE,)


@pytest.mark.parametrize(
    "state_fields",
    [
        {
            "status": CalibrationStatus.ACTIVE,
            "calibration_id": "invalid-active",
            "threshold": None,
            "artifact_sha256": None,
            "artifact": None,
        },
        {
            "status": CalibrationStatus.UNCALIBRATED,
            "calibration_id": None,
            "threshold": 0.75,
            "artifact_sha256": None,
            "artifact": None,
        },
    ],
)
def test_self_contradictory_calibration_state_fails_closed(
    state_fields: dict[str, object],
) -> None:
    calibration = _forge_calibration_state(**state_fields)
    decision = decide_image_verification(
        reference_reason_codes=(),
        probe_reason_codes=(),
        score=1.0,
        calibration=calibration,
    )

    assert decision.success is False
    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (ReasonCode.FEATURE_FAILED,)


def test_structurally_consistent_forged_active_state_cannot_pass(
    active_calibration: CalibrationState,
) -> None:
    forged = _forge_calibration_state(
        status=active_calibration.status,
        calibration_id=active_calibration.calibration_id,
        threshold=active_calibration.threshold,
        artifact_sha256=active_calibration.artifact_sha256,
        artifact=active_calibration.artifact,
    )

    decision = decide_image_verification(
        reference_reason_codes=(),
        probe_reason_codes=(),
        score=1.0,
        calibration=forged,
    )

    assert decision.success is False
    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (ReasonCode.FEATURE_FAILED,)
    assert decision.decision_threshold is None


def test_policy_cannot_invent_a_fourth_suggestion_state() -> None:
    assert tuple(Suggestion) == (
        Suggestion.PASS,
        Suggestion.RECAPTURE,
        Suggestion.REVIEW,
    )
