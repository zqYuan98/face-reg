from __future__ import annotations

import pytest

from face_algorithm.video.sampler import full_sample_indices, quick_sample_indices


def test_quick_sampling_is_deterministic_and_includes_first_and_last() -> None:
    assert quick_sample_indices(
        total_frames=100,
        sample_count=12,
        include_first_last=True,
    ) == (0, 9, 18, 27, 36, 45, 54, 63, 72, 81, 90, 99)


def test_quick_sampling_deduplicates_a_short_video() -> None:
    assert quick_sample_indices(
        total_frames=5,
        sample_count=12,
        include_first_last=True,
    ) == (0, 1, 2, 3, 4)


def test_quick_sampling_without_endpoints_uses_stratum_midpoints() -> None:
    assert quick_sample_indices(
        total_frames=100,
        sample_count=4,
        include_first_last=False,
    ) == (12, 37, 62, 87)


def test_full_sampling_uses_half_second_steps_and_hard_cap() -> None:
    assert full_sample_indices(
        total_frames=300,
        fps=30.0,
        interval_seconds=0.5,
        max_frames=10,
    ) == (0, 15, 30, 45, 60, 75, 90, 105, 120, 135)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_frames": 0, "sample_count": 12, "include_first_last": True},
        {"total_frames": 10, "sample_count": 0, "include_first_last": True},
        {"total_frames": 10, "sample_count": 2, "include_first_last": 1},
    ],
)
def test_quick_sampling_rejects_invalid_contracts(kwargs: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        quick_sample_indices(**kwargs)


@pytest.mark.parametrize("fps", [0.0, float("nan"), float("inf")])
def test_full_sampling_rejects_invalid_fps(fps: float) -> None:
    with pytest.raises(ValueError):
        full_sample_indices(
            total_frames=10,
            fps=fps,
            interval_seconds=0.5,
            max_frames=10,
        )
