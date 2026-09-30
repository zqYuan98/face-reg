"""Privacy-safe contracts for derived white-background portraits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np

from face_algorithm.schemas import ReasonCode, Suggestion

__all__ = [
    "PortraitMattingQuality",
    "PortraitPlacement",
    "PortraitResult",
]

PORTRAIT_SOFT_REASONS = frozenset({
    ReasonCode.TOO_DARK,
    ReasonCode.TOO_BRIGHT,
    ReasonCode.TOO_BLURRY,
    ReasonCode.BAD_POSE,
    ReasonCode.PORTRAIT_SOURCE_LIMITED,
    ReasonCode.PORTRAIT_UPSCALE_LIMITED,
})

# The older quality codes remain valid recapture reasons in strict mode.
# These two new codes, unlike those codes, are never a reason to fail alone.
_ARCHIVE_ONLY_WARNINGS = frozenset({
    ReasonCode.PORTRAIT_SOURCE_LIMITED,
    ReasonCode.PORTRAIT_UPSCALE_LIMITED,
})


@dataclass(frozen=True, slots=True)
class PortraitMattingQuality:
    """Aggregate quality facts; the raw matte is deliberately absent."""

    face_mean: float
    face_coverage: float
    subject_area_ratio: float
    shoulder_depth_ratio: float
    shoulder_band_coverage: float


@dataclass(frozen=True, slots=True)
class PortraitPlacement:
    """Auditable scale-and-translation placement in corrected-image space."""

    scale: float
    translate_x: float
    translate_y: float
    affine: tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True, slots=True)
class PortraitResult:
    """Public derived-image result with no biometric vector or raw matte."""

    success: bool
    suggestion: Suggestion
    reason_codes: tuple[ReasonCode, ...]
    model_versions: Mapping[str, str]
    config_version: str
    processing_time_ms: float
    trace_id: str
    template_version: str
    output_width: int
    output_height: int
    output_format: str
    jpeg_quality: int
    original_width: int | None
    original_height: int | None
    rotation_quarter_turns: int
    placement: PortraitPlacement | None
    matting_quality: PortraitMattingQuality | None
    image: np.ndarray | None = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(
            self,
            "model_versions",
            MappingProxyType(dict(self.model_versions)),
        )
        if self.processing_time_ms < 0 or not np.isfinite(self.processing_time_ms):
            raise ValueError("processing_time_ms must be finite and non-negative")
        if not self.trace_id or not self.config_version or not self.template_version:
            raise ValueError("result versions and trace_id must be non-empty")
        if self.output_width <= 0 or self.output_height <= 0:
            raise ValueError("portrait output dimensions must be positive")
        if self.success:
            if (
                self.image is None
                or self.placement is None
                or self.matting_quality is None
            ):
                raise ValueError("successful portrait result is incomplete")
            expected_suggestion = (
                Suggestion.REVIEW if self.reason_codes else Suggestion.PASS
            )
            if (
                self.suggestion is not expected_suggestion
                or not set(self.reason_codes).issubset(PORTRAIT_SOFT_REASONS)
            ):
                raise ValueError("successful portrait result has invalid quality warnings")
        elif (
            self.suggestion is Suggestion.PASS
            or not self.reason_codes
            or (
                set(self.reason_codes).issubset(PORTRAIT_SOFT_REASONS)
                and not (
                    self.suggestion is Suggestion.RECAPTURE
                    and set(self.reason_codes).isdisjoint(_ARCHIVE_ONLY_WARNINGS)
                )
            )
            or self.image is not None
            or self.placement is not None
            or self.matting_quality is not None
        ):
            raise ValueError("failed portrait result requires a rejection reason and no derived artifacts")
        if self.image is not None and not (
            isinstance(self.image, np.ndarray)
            and self.image.dtype == np.uint8
            and self.image.shape == (self.output_height, self.output_width, 3)
            and self.image.flags.c_contiguous
        ):
            raise ValueError("portrait image violates the uint8 BGR output contract")
