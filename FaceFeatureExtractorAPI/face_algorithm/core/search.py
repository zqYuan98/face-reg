"""Blocked, model-free ranking with the l2_dot_v1 matcher, for any one supported feature version."""

from __future__ import annotations

import time
from collections.abc import Sequence

import numpy as np

from face_algorithm.schemas import FEATURE_DIMENSIONS, ReasonCode

from .matcher import (
    EmbeddingMatchError,
    _normalized_copy,
    _require_feature_version,
    _SCORE_ROUNDOFF_TOLERANCE,
)


def rank_embeddings(
    probe: Sequence[float],
    candidates: Sequence[Sequence[float]],
    *,
    probe_version: str,
    reference_version: str,
    top_k: int,
    deadline: float,
    block_size: int = 128,
) -> list[tuple[int, float]]:
    """Return input indices and scores only after every candidate is valid.

    JSON validation belongs to the caller. Float32 conversion, float64 L2
    normalization and final clipping match the existing two-vector matcher.
    """
    version = _require_feature_version(probe_version)
    if _require_feature_version(reference_version) != version:
        raise EmbeddingMatchError(ReasonCode.FEATURE_VERSION_MISMATCH, "probe and candidate feature versions differ")
    if not candidates or not 1 <= top_k <= len(candidates) or block_size < 1:
        raise ValueError("invalid ranking bounds")
    probe_normalized = _normalized_copy(np.asarray(probe, dtype=np.float32), FEATURE_DIMENSIONS[version])
    scores = np.empty(len(candidates), dtype=np.float64)
    for start in range(0, len(candidates), block_size):
        if time.monotonic() >= deadline:
            raise TimeoutError("feature search deadline exceeded")
        end = min(start + block_size, len(candidates))
        block = np.asarray(candidates[start:end], dtype=np.float32).astype(np.float64)
        if block.shape != (end - start, probe_normalized.size) or not np.all(np.isfinite(block)):
            raise EmbeddingMatchError(ReasonCode.FEATURE_FAILED, "invalid candidate feature")
        norms = np.linalg.norm(block, axis=1)
        if not np.all(np.isfinite(norms)) or np.any(norms <= 0.0):
            raise EmbeddingMatchError(ReasonCode.FEATURE_FAILED, "invalid candidate norm")
        block /= norms[:, None]
        # einsum avoids multiplying BLAS thread pools on the two-CPU service.
        raw_scores = np.einsum("ij,j->i", block, probe_normalized, optimize=False)
        if (
            not np.all(np.isfinite(raw_scores))
            or np.any(raw_scores < -1.0 - _SCORE_ROUNDOFF_TOLERANCE)
            or np.any(raw_scores > 1.0 + _SCORE_ROUNDOFF_TOLERANCE)
        ):
            raise EmbeddingMatchError(ReasonCode.FEATURE_FAILED, "invalid candidate score")
        scores[start:end] = np.clip(raw_scores, -1.0, 1.0)
    if time.monotonic() >= deadline:
        raise TimeoutError("feature search deadline exceeded")
    # Sort the FINAL clipped scores: exact ties retain the original input order.
    indices = np.argsort(-scores, kind="stable")[:top_k]
    return [(int(index), float(scores[index])) for index in indices]
