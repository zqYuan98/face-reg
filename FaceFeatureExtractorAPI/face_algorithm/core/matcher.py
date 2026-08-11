"""Fail-closed matching for the frozen 1024-dimensional face protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np

from face_algorithm.schemas import (
    FEATURE_DIMENSION,
    FeatureVersion,
    ReasonCode,
)

MATCHER_VERSION: Final = "l2_dot_v1"
_SCORE_ROUNDOFF_TOLERANCE: Final = 1e-12

__all__ = [
    "MATCHER_VERSION",
    "EmbeddingMatchError",
    "MatchResult",
    "match_embeddings",
]


class EmbeddingMatchError(RuntimeError):
    """Structured internal error raised before an unsafe score can be used."""

    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class MatchResult:
    score: float
    matcher_version: Literal["l2_dot_v1"]
    feature_version: FeatureVersion
    feature_dimension: int


def _require_feature_version(value: object) -> None:
    if value != FeatureVersion.RECOGNIZER_V1_1024:
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_VERSION_MISMATCH,
            "embedding feature version must be recognizer_v1_1024",
        )


def _normalized_copy(vector: object) -> np.ndarray:
    if (
        not isinstance(vector, np.ndarray)
        or vector.dtype != np.dtype(np.float32)
        or vector.shape != (FEATURE_DIMENSION,)
    ):
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_VERSION_MISMATCH,
            "embedding must be a float32 ndarray with shape (1024,)",
        )
    if not np.all(np.isfinite(vector)):
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_FAILED,
            "embedding contains non-finite values",
        )
    normalized = vector.astype(np.float64, copy=True)
    norm = float(np.linalg.norm(normalized))
    if not np.isfinite(norm) or norm <= 0.0:
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_FAILED,
            "embedding norm must be finite and non-zero",
        )
    normalized /= norm
    return normalized


def match_embeddings(
    reference: object,
    probe: object,
    *,
    reference_version: FeatureVersion | str,
    probe_version: FeatureVersion | str,
) -> MatchResult:
    """Return a bounded dot-product score after validating both contracts."""

    _require_feature_version(reference_version)
    _require_feature_version(probe_version)
    reference_normalized = _normalized_copy(reference)
    probe_normalized = _normalized_copy(probe)
    raw_score = float(np.dot(reference_normalized, probe_normalized))
    if not np.isfinite(raw_score):
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_FAILED,
            "embedding similarity is not finite",
        )
    if (
        raw_score < -1.0 - _SCORE_ROUNDOFF_TOLERANCE
        or raw_score > 1.0 + _SCORE_ROUNDOFF_TOLERANCE
    ):
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_FAILED,
            "embedding similarity is outside the normalized score range",
        )
    return MatchResult(
        score=float(np.clip(raw_score, -1.0, 1.0)),
        matcher_version=MATCHER_VERSION,
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        feature_dimension=FEATURE_DIMENSION,
    )
