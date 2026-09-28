"""Privacy-safe observations and validated trial decisions for presence scans."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from face_algorithm.schemas import ReasonCode, ResultBase, Suggestion
from face_algorithm.presence.decision import PresenceDecision, decide_presence

__all__ = ["PresenceDecision", "PresenceEvidenceFrame", "VideoPresenceResult"]


QUALITY_WARNING_CODES = frozenset({ReasonCode.TOO_DARK, ReasonCode.TOO_BRIGHT,
    ReasonCode.TOO_BLURRY, ReasonCode.BAD_POSE, ReasonCode.FACE_TOO_SMALL, ReasonCode.FACE_CROPPED})


def _check_warnings(values: tuple[ReasonCode, ...]) -> tuple[ReasonCode, ...]:
    if len(set(values)) != len(values) or not set(values) <= QUALITY_WARNING_CODES:
        raise ValueError("quality warnings must be unique supported soft-quality codes")
    return values


class PresenceEvidenceFrame(BaseModel):
    """One scored frame; contains no image or biometric payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0, allow_inf_nan=False)
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    candidate_index: int = Field(default=0, ge=0, le=4)
    quality_warnings: tuple[ReasonCode, ...] = Field(default=(), max_length=6)

    @field_validator("quality_warnings")
    @classmethod
    def validate_warnings(cls, value: tuple[ReasonCode, ...]) -> tuple[ReasonCode, ...]:
        return _check_warnings(value)


class VideoPresenceResult(ResultBase):
    """A numeric comparison and optional configured, explicitly unvalidated decision."""

    mode: Literal["presence"] = "presence"
    decision: PresenceDecision = PresenceDecision.INCONCLUSIVE
    found: bool | None = None
    best_score: float | None = Field(default=None, ge=-1, le=1, allow_inf_nan=False)
    best_timestamp_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evidence_frames: tuple[PresenceEvidenceFrame, ...] = Field(default=(), max_length=5)
    scan_complete: bool
    sampling_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    sampled_frames: int = Field(ge=0)
    decoded_frames: int = Field(ge=0)
    valid_face_frames: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    candidate_limit_hit: bool
    duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    timestamp_source: Literal["pts", "estimated", "mixed", "unknown"]
    comparison_status: Literal["COMPARED", "NO_COMPARABLE_FACE", "INVALID_REFERENCE", "FAILED"]
    calibration_status: Literal["UNCALIBRATED", "CONFIGURED_UNVALIDATED"] = "UNCALIBRATED"
    threshold: float | None = Field(default=None, gt=-1, lt=1, allow_inf_nan=False)
    threshold_id: str | None = Field(default=None, min_length=1, max_length=128)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    sampling_strategy: Literal["presence_adaptive_v1"] = "presence_adaptive_v1"
    requested_sampling_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    base_frames: int = Field(ge=0)
    supplemental_frames: int = Field(ge=0)
    compared_candidates: int = Field(ge=0)
    failed_candidates: int = Field(ge=0)
    quality_warnings: tuple[ReasonCode, ...] = Field(default=(), max_length=6)
    feature_dimension: Literal[1024] = 1024
    matcher_version: Literal["l2_dot_v1"] = "l2_dot_v1"
    liveness_evaluated: Literal[False] = False

    @field_validator("quality_warnings")
    @classmethod
    def validate_warnings(cls, value: tuple[ReasonCode, ...]) -> tuple[ReasonCode, ...]:
        return _check_warnings(value)

    @field_validator("feature_dimension", mode="before")
    @classmethod
    def require_integer_dimension(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("feature dimension must be an integer")
        return value

    @field_validator("liveness_evaluated", mode="before")
    @classmethod
    def require_boolean_liveness(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("liveness evaluated must be boolean")
        return value

    @model_validator(mode="after")
    def validate_observations(self) -> Self:
        if self.suggestion is Suggestion.PASS:
            raise ValueError("presence trial decisions cannot suggest business PASS")
        if not self.success and self.suggestion is not Suggestion.REVIEW:
            raise ValueError("system failures require REVIEW")
        if self.scan_complete and (not self.success or self.sampled_frames == 0):
            raise ValueError("a complete scan requires successful sampled observations")
        if self.calibration_status == "UNCALIBRATED":
            if self.threshold is not None or self.threshold_id is not None:
                raise ValueError("uncalibrated results cannot carry a threshold")
            if ReasonCode.THRESHOLD_NOT_CONFIGURED not in self.reason_codes:
                raise ValueError("unconfigured threshold must be disclosed")
        elif self.threshold is None or self.threshold_id is None or not self.threshold_id.strip():
            raise ValueError("configured results require threshold metadata")
        elif ReasonCode.THRESHOLD_NOT_CONFIGURED in self.reason_codes:
            raise ValueError("configured results cannot report a missing threshold")
        if (self.comparison_status == "FAILED") != (not self.success):
            raise ValueError("failed comparison status must agree with success")
        if self.success:
            if self.compared_candidates + self.failed_candidates != self.candidate_count:
                raise ValueError("successful scans must account for every candidate")
            if (self.comparison_status == "COMPARED") != (self.compared_candidates > 0):
                raise ValueError("comparison status differs from actual comparisons")
            if self.comparison_status == "INVALID_REFERENCE" and self.sampled_frames:
                raise ValueError("invalid reference cannot have scanned video frames")
        if self.suggestion is Suggestion.RECAPTURE and self.comparison_status != "INVALID_REFERENCE":
            raise ValueError("only an invalid reference may request recapture")
        expected = decide_presence(success=self.success, scan_complete=self.scan_complete,
            comparison_status=self.comparison_status, best_score=self.best_score, threshold=self.threshold,
            candidate_limit_hit=self.candidate_limit_hit, failed_candidates=self.failed_candidates)
        if (self.decision, self.found) != expected:
            raise ValueError("decision differs from configured comparison state")
        if self.base_frames + self.supplemental_frames != self.sampled_frames:
            raise ValueError("base and supplemental counts must equal analyzed frames")
        if self.sampling_interval_seconds < self.requested_sampling_interval_seconds:
            raise ValueError("effective base interval cannot be smaller than requested")
        if not self.valid_face_frames <= self.compared_candidates <= self.candidate_count:
            raise ValueError("compared candidate count is inconsistent")
        if self.compared_candidates + self.failed_candidates > self.candidate_count:
            raise ValueError("candidate outcomes exceed attempts")
        if bool(self.compared_candidates) != bool(self.valid_face_frames):
            raise ValueError("candidate comparisons require comparable frames")
        if self.candidate_limit_hit != (ReasonCode.CANDIDATE_LIMIT_REACHED in self.reason_codes):
            raise ValueError("candidate limit flag and reason must agree")
        if not self.valid_face_frames <= self.sampled_frames <= self.decoded_frames:
            raise ValueError("scan frame counters are inconsistent")
        if not self.valid_face_frames <= self.candidate_count <= self.sampled_frames * 5:
            raise ValueError("candidate count exceeds the bounded analyzed candidates")
        if len(self.evidence_frames) > self.valid_face_frames:
            raise ValueError("evidence exceeds valid frame count")
        indices = [frame.frame_index for frame in self.evidence_frames]
        if len(set(indices)) != len(indices) or any(i >= self.decoded_frames for i in indices):
            raise ValueError("evidence frame indices must be unique and decoded")
        if (self.best_score is None) != (self.best_timestamp_seconds is None):
            raise ValueError("best score and timestamp must be present together")
        if bool(self.evidence_frames) != (self.best_score is not None):
            raise ValueError("best observation requires typed evidence")
        if bool(self.valid_face_frames) != bool(self.evidence_frames):
            raise ValueError("valid frames require evidence")
        if self.evidence_frames:
            ordered = tuple(sorted(self.evidence_frames, key=lambda frame: (
                -frame.score, len(frame.quality_warnings), frame.timestamp_seconds, frame.frame_index)))
            if ordered != self.evidence_frames:
                raise ValueError("evidence must follow deterministic score/quality/time order")
            best = ordered[0]
            if self.best_score != best.score or self.best_timestamp_seconds != best.timestamp_seconds:
                raise ValueError("best observation differs from evidence")
            if any(not set(frame.quality_warnings) <= set(self.quality_warnings) for frame in ordered):
                raise ValueError("evidence warnings must be included in result warnings")
        if self.duration_seconds is not None and any(
            frame.timestamp_seconds > self.duration_seconds for frame in self.evidence_frames
        ):
            raise ValueError("evidence timestamp exceeds scanned duration")
        return self
