"""Immutable best-effort sampling and explicit trial decision policy."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["VideoPresencePolicy"]


class VideoPresencePolicy(BaseModel):
    """Sampling and quality floors; these are not calibrated identity evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    mode: Literal["uncalibrated", "configured"] = "uncalibrated"
    threshold: float | None = Field(default=None, gt=-1, lt=1, allow_inf_nan=False)
    threshold_id: str | None = Field(default=None, min_length=1, max_length=128)
    max_supplemental_frames: int = Field(default=2, ge=0, le=2)
    sample_interval_seconds: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    max_analyzed_frames: int = Field(default=1200, gt=0)
    max_face_candidates: int = Field(default=5, ge=1, le=5)
    max_evidence_frames: int = Field(default=5, ge=1, le=5)
    reference_min_face_size: int = Field(default=112, gt=0)
    video_min_face_size: int = Field(default=64, gt=0)

    @model_validator(mode="after")
    def validate_threshold_mode(self) -> Self:
        if self.mode == "configured":
            if self.threshold is None or self.threshold_id is None or not self.threshold_id.strip():
                raise ValueError("configured presence requires an explicit threshold and threshold_id")
        elif self.threshold is not None or self.threshold_id is not None:
            raise ValueError("uncalibrated presence cannot carry threshold metadata")
        return self
