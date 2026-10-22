"""Fail-closed matching of two features of the same, supported feature version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import numpy as np

from face_algorithm.schemas import (
    FEATURE_DIMENSIONS,
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


def _require_feature_version(value: object) -> FeatureVersion:
    try:
        return FeatureVersion(value)
    except ValueError:
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_VERSION_MISMATCH,
            "unsupported embedding feature version",
        ) from None


def _normalized_copy(vector: object, dimension: int) -> np.ndarray:
    if (
        not isinstance(vector, np.ndarray)
        or vector.dtype != np.dtype(np.float32)
        or vector.shape != (dimension,)
    ):
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_VERSION_MISMATCH,
            f"embedding must be a float32 ndarray with shape ({dimension},)",
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

    version = _require_feature_version(reference_version)
    if _require_feature_version(probe_version) != version:
        # Features of different models live in different spaces; their dot product means nothing.
        raise EmbeddingMatchError(
            ReasonCode.FEATURE_VERSION_MISMATCH,
            "reference and probe feature versions differ",
        )
    dimension = FEATURE_DIMENSIONS[version]
    reference_normalized = _normalized_copy(reference, dimension)
    probe_normalized = _normalized_copy(probe, dimension)
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
        feature_version=version,
        feature_dimension=dimension,
    )
