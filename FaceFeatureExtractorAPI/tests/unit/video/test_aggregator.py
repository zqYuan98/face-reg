from __future__ import annotations

import math

import pytest

from face_algorithm.video.aggregator import aggregate_scores, longest_no_face_gap


def test_score_aggregation_reports_robust_statistics_and_pass_ratio() -> None:
    result = aggregate_scores(
        (0.1, 0.8, 0.9, 1.0),
        frame_threshold=0.75,
        percentile=0.10,
    )

    assert result.valid_count == 4
    assert result.median_score == pytest.approx(0.85)
    assert result.percentile_score == pytest.approx(0.31)
    assert result.minimum_score == pytest.approx(0.10)
    assert result.pass_ratio == pytest.approx(0.75)


def test_empty_score_aggregation_has_no_identity_metrics() -> None:
    result = aggregate_scores((), frame_threshold=0.75, percentile=0.10)

    assert result.valid_count == 0
    assert result.median_score is None
    assert result.percentile_score is None
    assert result.minimum_score is None
    assert result.pass_ratio is None


def test_uncalibrated_aggregation_reports_scores_without_a_pass_ratio() -> None:
    result = aggregate_scores(
        (0.8, 0.9),
        frame_threshold=None,
        percentile=0.10,
    )

    assert result.percentile_score == pytest.approx(0.81)
    assert result.pass_ratio is None


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1.1, 1.1, True])
def test_score_aggregation_rejects_invalid_scores(score: float) -> None:
    with pytest.raises((TypeError, ValueError)):
        aggregate_scores((score,), frame_threshold=0.75, percentile=0.10)


def test_longest_no_face_gap_covers_middle_and_trailing_absence() -> None:
    assert longest_no_face_gap(
        timestamps=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5),
        no_face=(False, True, True, False, True, True),
        duration_seconds=3.0,
    ) == pytest.approx(1.0)


def test_no_face_gap_rejects_non_monotonic_timestamps() -> None:
    with pytest.raises(ValueError, match="increasing"):
        longest_no_face_gap(
            timestamps=(0.0, 1.0, 0.5),
            no_face=(False, True, True),
            duration_seconds=2.0,
        )
