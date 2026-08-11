from __future__ import annotations

import numpy as np
import pytest

from face_algorithm.schemas import FaceBox
from face_algorithm.video.tracker import (
    FrameObservation,
    TrackingPolicy,
    build_tracks,
)


def _embedding(index: int = 0) -> np.ndarray:
    value = np.zeros(1024, dtype=np.float32)
    value[index] = 1.0
    return value


def _observation(
    frame_index: int,
    *,
    identity: int = 0,
    dx: float = 0.0,
) -> FrameObservation:
    box = FaceBox(x1=100 + dx, y1=80, x2=220 + dx, y2=240)
    landmarks = np.asarray(
        [
            [135 + dx, 125],
            [185 + dx, 125],
            [160 + dx, 155],
            [140 + dx, 200],
            [180 + dx, 200],
        ],
        dtype=np.float32,
    )
    return FrameObservation(
        frame_index=frame_index,
        timestamp_seconds=frame_index * 0.5,
        box=box,
        landmarks=landmarks,
        embedding=_embedding(identity),
        similarity_to_reference=0.90 if identity == 0 else 0.10,
    )


def _policy(max_secondary_ratio: float = 0.20) -> TrackingPolicy:
    return TrackingPolicy(
        iou_threshold=0.30,
        landmark_distance_threshold=0.15,
        embedding_threshold=0.55,
        min_track_coverage=0.70,
        max_secondary_track_ratio=max_secondary_ratio,
    )


def test_stable_subject_builds_one_track_with_full_coverage() -> None:
    observations = tuple(_observation(index, dx=index * 2.0) for index in range(6))

    result = build_tracks(observations, sampled_frame_count=6, policy=_policy())

    assert len(result.tracks) == 1
    assert result.primary_track_id == 0
    assert result.primary_coverage == 1.0
    assert result.secondary_track_ratio == 0.0
    assert result.subject_switched is False


def test_high_iou_never_joins_an_embedding_incompatible_replacement() -> None:
    observations = (_observation(0, identity=0), _observation(1, identity=1))

    result = build_tracks(
        observations,
        sampled_frame_count=2,
        policy=_policy(max_secondary_ratio=0.10),
    )

    assert len(result.tracks) == 2
    assert result.secondary_track_ratio == 0.5
    assert result.subject_switched is True


def test_sequential_subject_change_is_reported_even_with_one_face_per_frame() -> None:
    observations = tuple(
        [_observation(index, identity=0) for index in range(5)]
        + [_observation(index, identity=1) for index in range(5, 10)]
    )

    result = build_tracks(
        observations,
        sampled_frame_count=10,
        policy=_policy(max_secondary_ratio=0.10),
    )

    assert len(result.tracks) == 2
    assert result.primary_coverage == 0.5
    assert result.secondary_track_ratio == 0.5
    assert result.subject_switched is True


def test_brief_secondary_outlier_below_policy_ratio_does_not_claim_switch() -> None:
    observations = tuple(
        [_observation(index, identity=0) for index in range(9)]
        + [_observation(9, identity=1)]
    )

    result = build_tracks(
        observations,
        sampled_frame_count=10,
        policy=_policy(max_secondary_ratio=0.20),
    )

    assert result.primary_coverage == 0.9
    assert result.secondary_track_ratio == 0.1
    assert result.subject_switched is False


def test_observation_rejects_a_non_1024_embedding() -> None:
    with pytest.raises(ValueError, match="1024"):
        FrameObservation(
            frame_index=0,
            timestamp_seconds=0.0,
            box=FaceBox(x1=1, y1=1, x2=10, y2=10),
            landmarks=np.ones((5, 2), dtype=np.float32),
            embedding=np.ones(512, dtype=np.float32),
            similarity_to_reference=0.5,
        )


def test_tracker_does_not_mutate_caller_arrays() -> None:
    observation = _observation(0)
    landmarks_before = observation.landmarks.copy()
    embedding_before = observation.embedding.copy()

    build_tracks((observation,), sampled_frame_count=1, policy=_policy())

    np.testing.assert_array_equal(observation.landmarks, landmarks_before)
    np.testing.assert_array_equal(observation.embedding, embedding_before)
