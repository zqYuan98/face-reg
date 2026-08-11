"""Privacy-safe contracts for witness-photo background blur."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np

from face_algorithm.schemas import CardRegion, ReasonCode, Suggestion

__all__ = [
    "WitnessBlurResult",
    "WitnessBlurSummary",
    "WitnessMattingQuality",
]


@dataclass(frozen=True, slots=True)
class WitnessMattingQuality:
    """Aggregate matting evidence; raw alpha is deliberately absent."""

    face_mean: float
    face_coverage: float
    subject_area_ratio: float


@dataclass(frozen=True, slots=True)
class WitnessBlurSummary:
    """Non-sensitive facts about the applied deterministic blur."""

    protected_area_ratio: float
    sigma_pixels: float
    kernel_size: int


@dataclass(frozen=True, slots=True)
class WitnessBlurResult:
    """Public result containing only a derived image and safe summaries."""

    success: bool
    suggestion: Suggestion
    reason_codes: tuple[ReasonCode, ...]
    model_versions: Mapping[str, str]
    config_version: str
    processing_time_ms: float
    trace_id: str
    original_width: int | None
    original_height: int | None
    rotation_quarter_turns: int
    coordinate_space: str
    card_regions: tuple[CardRegion, ...]
    card_candidate_count: int
    protected_area_ratio: float
    matting_quality: WitnessMattingQuality | None
    blur_summary: WitnessBlurSummary | None
    image: np.ndarray | None = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "card_regions", tuple(self.card_regions))
        object.__setattr__(
            self, "model_versions", MappingProxyType(dict(self.model_versions))
        )
        if not self.trace_id or not self.config_version:
            raise ValueError("result identifiers must be non-empty")
        if self.coordinate_space != "original_image":
            raise ValueError("witness card coordinates must use original_image")
        if not np.isfinite(self.processing_time_ms) or self.processing_time_ms < 0:
            raise ValueError("processing_time_ms must be finite and non-negative")
        if not 0.0 <= self.protected_area_ratio <= 1.0:
            raise ValueError("protected_area_ratio must be in [0, 1]")
        if self.card_candidate_count < len(self.card_regions):
            raise ValueError("candidate count cannot be below trusted card count")
        if self.success:
            if (
                self.suggestion is not Suggestion.PASS
                or self.reason_codes
                or self.image is None
                or self.matting_quality is None
                or self.blur_summary is None
            ):
                raise ValueError("successful witness blur result is incomplete")
        elif self.suggestion is Suggestion.PASS or not self.reason_codes:
            raise ValueError("failed witness blur requires a reason and action")
        if self.image is not None:
            if self.original_height is None or self.original_width is None:
                raise ValueError("derived image requires original dimensions")
            if not (
                isinstance(self.image, np.ndarray)
                and self.image.dtype == np.uint8
                and self.image.shape
                == (self.original_height, self.original_width, 3)
                and self.image.flags.c_contiguous
            ):
                raise ValueError("witness image violates original-size uint8 BGR contract")
