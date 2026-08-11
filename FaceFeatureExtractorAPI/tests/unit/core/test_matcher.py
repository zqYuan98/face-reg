from __future__ import annotations

import math

import numpy as np
import pytest

from face_algorithm.core.matcher import (
    MATCHER_VERSION,
    EmbeddingMatchError,
    match_embeddings,
)
from face_algorithm.schemas import FeatureVersion, ReasonCode


FEATURE_VERSION = FeatureVersion.RECOGNIZER_V1_1024


def _unit_vector(index: int = 0) -> np.ndarray:
    vector = np.zeros(1024, dtype=np.float32)
    vector[index] = 1.0
    return vector


def test_matcher_re_normalizes_in_float64_without_mutating_inputs() -> None:
    reference = np.linspace(1.0, 2.0, 1024, dtype=np.float32)
    probe = np.linspace(2.0, 1.0, 1024, dtype=np.float32)
    reference_before = reference.copy()
    probe_before = probe.copy()

    result = match_embeddings(
        reference,
        probe,
        reference_version=FEATURE_VERSION,
        probe_version=FEATURE_VERSION,
    )

    expected = float(
        np.dot(
            reference.astype(np.float64) / np.linalg.norm(reference.astype(np.float64)),
            probe.astype(np.float64) / np.linalg.norm(probe.astype(np.float64)),
        )
    )
    assert result.score == pytest.approx(expected, abs=1e-12)
    assert result.matcher_version == "l2_dot_v1" == MATCHER_VERSION
    assert result.feature_version is FEATURE_VERSION
    assert result.feature_dimension == 1024
    assert np.array_equal(reference, reference_before)
    assert np.array_equal(probe, probe_before)


def test_identical_and_orthogonal_embeddings_have_known_scores() -> None:
    first = _unit_vector(0)
    second = _unit_vector(1)

    identical = match_embeddings(
        first,
        first,
        reference_version=FEATURE_VERSION,
        probe_version=FEATURE_VERSION,
    )
    orthogonal = match_embeddings(
        first,
        second,
        reference_version=FEATURE_VERSION,
        probe_version=FEATURE_VERSION,
    )

    assert identical.score == pytest.approx(1.0)
    assert orthogonal.score == pytest.approx(0.0)


@pytest.mark.parametrize(
    "reference,probe",
    [
        (list(np.ones(1024, dtype=np.float32)), _unit_vector()),
        (np.ones(1024, dtype=np.float64), _unit_vector()),
        (np.ones(1023, dtype=np.float32), _unit_vector()),
        (np.ones((1, 1024), dtype=np.float32), _unit_vector()),
        (_unit_vector(), np.ones(1025, dtype=np.float32)),
    ],
)
def test_invalid_embedding_contract_fails_closed(reference, probe) -> None:
    with pytest.raises(EmbeddingMatchError) as caught:
        match_embeddings(
            reference,
            probe,
            reference_version=FEATURE_VERSION,
            probe_version=FEATURE_VERSION,
        )

    assert caught.value.reason_code is ReasonCode.FEATURE_VERSION_MISMATCH


@pytest.mark.parametrize(
    "vector",
    [
        np.zeros(1024, dtype=np.float32),
        np.full(1024, np.nan, dtype=np.float32),
        np.full(1024, np.inf, dtype=np.float32),
    ],
)
def test_non_finite_or_zero_embedding_fails_closed(vector: np.ndarray) -> None:
    with pytest.raises(EmbeddingMatchError) as caught:
        match_embeddings(
            vector,
            _unit_vector(),
            reference_version=FEATURE_VERSION,
            probe_version=FEATURE_VERSION,
        )

    assert caught.value.reason_code is ReasonCode.FEATURE_FAILED


@pytest.mark.parametrize(
    ("reference_version", "probe_version"),
    [
        ("recognizer_v0_1024", FEATURE_VERSION),
        (FEATURE_VERSION, "recognizer_v0_1024"),
        ("recognizer_v1_1024", "recognizer_v1_512"),
    ],
)
def test_feature_version_mismatch_is_rejected_before_scoring(
    reference_version,
    probe_version,
) -> None:
    with pytest.raises(EmbeddingMatchError) as caught:
        match_embeddings(
            _unit_vector(),
            _unit_vector(),
            reference_version=reference_version,
            probe_version=probe_version,
        )

    assert caught.value.reason_code is ReasonCode.FEATURE_VERSION_MISMATCH


def test_score_is_always_finite_and_bounded() -> None:
    reference = np.linspace(-3.0, 5.0, 1024, dtype=np.float32)
    probe = -reference

    result = match_embeddings(
        reference,
        probe,
        reference_version=FEATURE_VERSION,
        probe_version=FEATURE_VERSION,
    )

    assert math.isfinite(result.score)
    assert -1.0 <= result.score <= 1.0
    assert result.score == pytest.approx(-1.0)


def test_only_roundoff_sized_score_overflow_is_clamped(monkeypatch) -> None:
    import face_algorithm.core.matcher as matcher

    monkeypatch.setattr(matcher.np, "dot", lambda *_args: 1.0 + 1e-13)
    result = match_embeddings(
        _unit_vector(),
        _unit_vector(),
        reference_version=FEATURE_VERSION,
        probe_version=FEATURE_VERSION,
    )
    assert result.score == 1.0

    monkeypatch.setattr(matcher.np, "dot", lambda *_args: 1.01)
    with pytest.raises(EmbeddingMatchError) as caught:
        match_embeddings(
            _unit_vector(),
            _unit_vector(),
            reference_version=FEATURE_VERSION,
            probe_version=FEATURE_VERSION,
        )
    assert caught.value.reason_code is ReasonCode.FEATURE_FAILED
