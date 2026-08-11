"""Privacy-safe contracts for derived head-and-shoulders portraits."""

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
                self.suggestion is not Suggestion.PASS
                or self.reason_codes
                or self.image is None
                or self.placement is None
                or self.matting_quality is None
            ):
                raise ValueError("successful portrait result is incomplete")
        elif self.suggestion is Suggestion.PASS or not self.reason_codes:
            raise ValueError("failed portrait result requires a reason and action")
        if self.image is not None and not (
            isinstance(self.image, np.ndarray)
            and self.image.dtype == np.uint8
            and self.image.shape == (self.output_height, self.output_width, 3)
            and self.image.flags.c_contiguous
        ):
            raise ValueError("portrait image violates the uint8 BGR output contract")
