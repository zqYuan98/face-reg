"""Strict operational limits, without a calibrated identity threshold."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["VideoPresencePolicy"]


class VideoPresencePolicy(BaseModel):
    """Sampling and quality floors; these are not calibrated identity evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    mode: Literal["uncalibrated"] = "uncalibrated"
    sample_interval_seconds: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    max_analyzed_frames: int = Field(default=1200, gt=0)
    max_face_candidates: int = Field(default=5, ge=1, le=5)
    max_evidence_frames: int = Field(default=5, ge=1, le=5)
    reference_min_face_size: int = Field(default=112, gt=0)
    video_min_face_size: int = Field(default=64, gt=0)
