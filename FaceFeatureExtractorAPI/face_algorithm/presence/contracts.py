"""Typed, privacy-safe observations from an uncalibrated presence scan."""

from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from face_algorithm.schemas import ReasonCode, ResultBase, Suggestion

__all__ = ["PresenceDecision", "PresenceEvidenceFrame", "VideoPresenceResult"]


class PresenceDecision(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND_IN_SCAN = "NOT_FOUND_IN_SCAN"
    INCONCLUSIVE = "INCONCLUSIVE"


class PresenceEvidenceFrame(BaseModel):
    """One scored frame; contains no image or biometric payload."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(ge=0, allow_inf_nan=False)
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)


class VideoPresenceResult(ResultBase):
    """Observational scan result, never an identity decision before calibration."""

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
    calibration_status: Literal["UNCALIBRATED"] = "UNCALIBRATED"
    feature_dimension: Literal[1024] = 1024
    matcher_version: Literal["l2_dot_v1"] = "l2_dot_v1"
    liveness_evaluated: Literal[False] = False

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
        if self.decision is not PresenceDecision.INCONCLUSIVE or self.found is not None:
            raise ValueError("uncalibrated scans cannot make an identity decision")
        if self.suggestion is Suggestion.PASS:
            raise ValueError("uncalibrated scans cannot suggest PASS")
        if not self.success and self.suggestion is not Suggestion.REVIEW:
            raise ValueError("system failures require REVIEW")
        if self.scan_complete and (not self.success or self.sampled_frames == 0):
            raise ValueError("a complete scan requires successful sampled observations")
        required = {ReasonCode.CALIBRATION_REQUIRED, ReasonCode.THRESHOLD_UNCALIBRATED}
        if not required.issubset(self.reason_codes):
            raise ValueError("uncalibrated scans must disclose missing calibration")
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
            best = min(self.evidence_frames, key=lambda frame: (-frame.score, frame.timestamp_seconds, frame.frame_index))
            if self.best_score != best.score or self.best_timestamp_seconds != best.timestamp_seconds:
                raise ValueError("best observation differs from evidence")
        if self.duration_seconds is not None and any(
            frame.timestamp_seconds > self.duration_seconds for frame in self.evidence_frames
        ):
            raise ValueError("evidence timestamp exceeds scanned duration")
        return self
