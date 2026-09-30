"""Pure, fail-closed decision policy for two-image identity verification."""

from __future__ import annotations

import math
from dataclasses import dataclass

from face_algorithm.runtime.calibration import (
    CalibrationState,
    CalibrationStatus,
    ImageCalibrationArtifact,
    _is_loader_issued_calibration,
)
from face_algorithm.schemas import (
    IMAGE_RECAPTURE_REASON_CODES,
    ReasonCode,
    Suggestion,
)
from face_algorithm.verification.configured_threshold import (
    ConfiguredThresholdState,
    _is_issued_configured_threshold,
)

__all__ = ["ImageVerificationDecision", "decide_image_verification"]


@dataclass(frozen=True, slots=True)
class ImageVerificationDecision:
    success: bool
    suggestion: Suggestion
    reason_codes: tuple[ReasonCode, ...]
    decision_threshold: float | None


def _stable_reasons(
    reference: tuple[ReasonCode, ...],
    probe: tuple[ReasonCode, ...],
) -> tuple[ReasonCode, ...] | None:
    merged: list[ReasonCode] = []
    seen: set[ReasonCode] = set()
    for reason in (*reference, *probe):
        if not isinstance(reason, ReasonCode):
            return None
        if reason in {
            ReasonCode.IMAGE_TRIAL_THRESHOLD_MATCHED,
            ReasonCode.IMAGE_TRIAL_THRESHOLD_NOT_MATCHED,
        }:
            return None
        if reason not in seen:
            seen.add(reason)
            merged.append(reason)
    return tuple(merged)


def _valid_calibration(state: CalibrationState | ConfiguredThresholdState) -> bool:
    if isinstance(state, ConfiguredThresholdState):
        return _is_issued_configured_threshold(state)
    if not _is_loader_issued_calibration(state):
        return False
    if state.status is CalibrationStatus.UNCALIBRATED:
        return (
            state.calibration_id is None
            and state.threshold is None
            and state.artifact_sha256 is None
            and state.artifact is None
        )
    if state.status is not CalibrationStatus.ACTIVE:
        return False
    artifact = state.artifact
    threshold = state.threshold
    return bool(
        isinstance(artifact, ImageCalibrationArtifact)
        and getattr(state, "video_thresholds", None) is None
        and isinstance(state.calibration_id, str)
        and state.calibration_id
        and state.calibration_id == artifact.calibration_id
        and isinstance(state.artifact_sha256, str)
        and len(state.artifact_sha256) == 64
        and all(character in "0123456789abcdef" for character in state.artifact_sha256)
        and not isinstance(threshold, bool)
        and isinstance(threshold, (int, float))
        and math.isfinite(float(threshold))
        and 0.0 <= float(threshold) <= 1.0
        and float(threshold) == artifact.threshold
    )


def _failed_closed() -> ImageVerificationDecision:
    return ImageVerificationDecision(
        success=False,
        suggestion=Suggestion.REVIEW,
        reason_codes=(ReasonCode.FEATURE_FAILED,),
        decision_threshold=None,
    )


def decide_image_verification(
    *,
    reference_reason_codes: tuple[ReasonCode, ...],
    probe_reason_codes: tuple[ReasonCode, ...],
    score: float | None,
    calibration: CalibrationState | ConfiguredThresholdState,
) -> ImageVerificationDecision:
    """Apply hard-error, recapture, calibration, then threshold precedence."""

    if not _valid_calibration(calibration):
        return _failed_closed()
    reasons = _stable_reasons(reference_reason_codes, probe_reason_codes)
    if reasons is None:
        return _failed_closed()
    decision_threshold = (
        float(calibration.threshold)
        if calibration.threshold is not None
        else None
    )

    if any(reason not in IMAGE_RECAPTURE_REASON_CODES for reason in reasons):
        return ImageVerificationDecision(
            success=False,
            suggestion=Suggestion.REVIEW,
            reason_codes=reasons,
            decision_threshold=decision_threshold,
        )
    if reasons:
        return ImageVerificationDecision(
            success=True,
            suggestion=Suggestion.RECAPTURE,
            reason_codes=reasons,
            decision_threshold=decision_threshold,
        )

    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not -1.0 <= float(score) <= 1.0
    ):
        return _failed_closed()
    score = float(score)

    if isinstance(calibration, ConfiguredThresholdState):
        assert decision_threshold is not None
        reason = (
            ReasonCode.IMAGE_TRIAL_THRESHOLD_MATCHED
            if score >= decision_threshold
            else ReasonCode.IMAGE_TRIAL_THRESHOLD_NOT_MATCHED
        )
        return ImageVerificationDecision(
            success=True,
            suggestion=Suggestion.REVIEW,
            reason_codes=(reason,),
            decision_threshold=decision_threshold,
        )

    if calibration.status is CalibrationStatus.UNCALIBRATED:
        return ImageVerificationDecision(
            success=True,
            suggestion=Suggestion.REVIEW,
            reason_codes=(ReasonCode.THRESHOLD_UNCALIBRATED,),
            decision_threshold=None,
        )
    assert decision_threshold is not None
    if score >= decision_threshold:
        return ImageVerificationDecision(
            success=True,
            suggestion=Suggestion.PASS,
            reason_codes=(),
            decision_threshold=decision_threshold,
        )
    return ImageVerificationDecision(
        success=True,
        suggestion=Suggestion.REVIEW,
        reason_codes=(ReasonCode.IDENTITY_UNCERTAIN,),
        decision_threshold=decision_threshold,
    )
