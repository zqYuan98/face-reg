"""Deterministic association for valid single-face video observations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from face_algorithm.schemas import FEATURE_DIMENSION, FaceBox

__all__ = [
    "FrameObservation",
    "SubjectTrack",
    "TrackingPolicy",
    "TrackingResult",
    "build_tracks",
]


@dataclass(frozen=True, slots=True)
class TrackingPolicy:
    iou_threshold: float
    landmark_distance_threshold: float
    embedding_threshold: float
    min_track_coverage: float
    max_secondary_track_ratio: float

    def __post_init__(self) -> None:
        for label in (
            "iou_threshold",
            "embedding_threshold",
            "min_track_coverage",
            "max_secondary_track_ratio",
        ):
            value = getattr(self, label)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{label} must be finite and in [0, 1]")
        if (
            isinstance(self.landmark_distance_threshold, bool)
            or not isinstance(self.landmark_distance_threshold, (int, float))
            or not math.isfinite(float(self.landmark_distance_threshold))
            or self.landmark_distance_threshold <= 0.0
        ):
            raise ValueError("landmark distance threshold must be finite and positive")


@dataclass(frozen=True, slots=True, repr=False)
class FrameObservation:
    frame_index: int
    timestamp_seconds: float
    box: FaceBox
    landmarks: np.ndarray = field(repr=False)
    embedding: np.ndarray = field(repr=False)
    similarity_to_reference: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.frame_index, bool)
            or not isinstance(self.frame_index, int)
            or self.frame_index < 0
        ):
            raise ValueError("frame index must be a non-negative integer")
        if (
            isinstance(self.timestamp_seconds, bool)
            or not isinstance(self.timestamp_seconds, (int, float))
            or not math.isfinite(float(self.timestamp_seconds))
            or self.timestamp_seconds < 0.0
        ):
            raise ValueError("timestamp must be finite and non-negative")
        if not isinstance(self.box, FaceBox):
            raise TypeError("box must be a FaceBox")
        if (
            not isinstance(self.landmarks, np.ndarray)
            or self.landmarks.dtype != np.float32
            or self.landmarks.shape != (5, 2)
            or not np.all(np.isfinite(self.landmarks))
        ):
            raise ValueError("landmarks must be a finite float32 (5, 2) array")
        if (
            not isinstance(self.embedding, np.ndarray)
            or self.embedding.dtype != np.float32
            or self.embedding.shape != (FEATURE_DIMENSION,)
            or not np.all(np.isfinite(self.embedding))
        ):
            raise ValueError("embedding must be a finite float32 1024-vector")
        norm = float(np.linalg.norm(self.embedding.astype(np.float64, copy=False)))
        if not math.isfinite(norm) or not math.isclose(
            norm,
            1.0,
            rel_tol=1e-5,
            abs_tol=1e-6,
        ):
            raise ValueError("embedding must be L2-normalized")
        if (
            isinstance(self.similarity_to_reference, bool)
            or not isinstance(self.similarity_to_reference, (int, float))
            or not math.isfinite(float(self.similarity_to_reference))
            or not -1.0 <= float(self.similarity_to_reference) <= 1.0
        ):
            raise ValueError("reference similarity must be finite and in [-1, 1]")
        landmarks = self.landmarks.copy()
        embedding = self.embedding.copy()
        landmarks.setflags(write=False)
        embedding.setflags(write=False)
        object.__setattr__(self, "landmarks", landmarks)
        object.__setattr__(self, "embedding", embedding)
        object.__setattr__(self, "timestamp_seconds", float(self.timestamp_seconds))
        object.__setattr__(
            self,
            "similarity_to_reference",
            float(self.similarity_to_reference),
        )


@dataclass(frozen=True, slots=True)
class SubjectTrack:
    track_id: int
    observations: tuple[FrameObservation, ...] = field(repr=False)

    @property
    def first_frame_index(self) -> int:
        return self.observations[0].frame_index

    @property
    def last_frame_index(self) -> int:
        return self.observations[-1].frame_index


@dataclass(frozen=True, slots=True)
class TrackingResult:
    tracks: tuple[SubjectTrack, ...] = field(repr=False)
    primary_track_id: int | None
    primary_coverage: float
    secondary_track_ratio: float
    primary_meets_coverage: bool
    subject_switched: bool

    @property
    def primary_track(self) -> SubjectTrack | None:
        if self.primary_track_id is None:
            return None
        return self.tracks[self.primary_track_id]


def _box_iou(left: FaceBox, right: FaceBox) -> float:
    intersection_width = max(0.0, min(left.x2, right.x2) - max(left.x1, right.x1))
    intersection_height = max(0.0, min(left.y2, right.y2) - max(left.y1, right.y1))
    intersection = intersection_width * intersection_height
    union = left.width * left.height + right.width * right.height - intersection
    return 0.0 if union <= 0.0 else intersection / union


def _landmark_distance(left: FrameObservation, right: FrameObservation) -> float:
    displacement = left.landmarks.astype(np.float64) - right.landmarks.astype(np.float64)
    rms = float(np.sqrt(np.mean(np.sum(displacement * displacement, axis=1))))
    left_diagonal = math.hypot(left.box.width, left.box.height)
    right_diagonal = math.hypot(right.box.width, right.box.height)
    scale = max((left_diagonal + right_diagonal) * 0.5, 1e-12)
    return rms / scale


def _embedding_similarity(left: FrameObservation, right: FrameObservation) -> float:
    return float(
        np.dot(
            left.embedding.astype(np.float64, copy=False),
            right.embedding.astype(np.float64, copy=False),
        )
    )


def _association_score(
    previous: FrameObservation,
    current: FrameObservation,
    policy: TrackingPolicy,
) -> float | None:
    embedding_similarity = _embedding_similarity(previous, current)
    if embedding_similarity < policy.embedding_threshold:
        return None
    iou = _box_iou(previous.box, current.box)
    landmark_distance = _landmark_distance(previous, current)
    if (
        iou < policy.iou_threshold
        and landmark_distance > policy.landmark_distance_threshold
    ):
        return None
    landmark_score = max(
        0.0,
        1.0 - landmark_distance / policy.landmark_distance_threshold,
    )
    return embedding_similarity + iou + landmark_score


def build_tracks(
    observations: tuple[FrameObservation, ...],
    *,
    sampled_frame_count: int,
    policy: TrackingPolicy,
) -> TrackingResult:
    """Greedily associate one valid observation per sampled frame."""

    if isinstance(sampled_frame_count, bool) or not isinstance(sampled_frame_count, int):
        raise TypeError("sampled_frame_count must be a positive integer")
    if sampled_frame_count <= 0:
        raise ValueError("sampled_frame_count must be a positive integer")
    if not isinstance(policy, TrackingPolicy):
        raise TypeError("policy must be a TrackingPolicy")
    if not isinstance(observations, tuple) or not all(
        isinstance(observation, FrameObservation) for observation in observations
    ):
        raise TypeError("observations must be a tuple of FrameObservation")
    ordered = tuple(sorted(observations, key=lambda item: item.frame_index))
    if len(ordered) > sampled_frame_count:
        raise ValueError("valid observations cannot exceed sampled frames")
    if len({item.frame_index for item in ordered}) != len(ordered):
        raise ValueError("only one valid observation is allowed per frame")
    if any(
        current.timestamp_seconds < previous.timestamp_seconds
        for previous, current in zip(ordered, ordered[1:])
    ):
        raise ValueError("observation timestamps must increase with frame indices")

    mutable_tracks: list[list[FrameObservation]] = []
    for observation in ordered:
        best_track: int | None = None
        best_score = -math.inf
        for track_id, track in enumerate(mutable_tracks):
            score = _association_score(track[-1], observation, policy)
            if score is not None and score > best_score:
                best_track = track_id
                best_score = score
        if best_track is None:
            mutable_tracks.append([observation])
        else:
            mutable_tracks[best_track].append(observation)

    tracks = tuple(
        SubjectTrack(track_id=index, observations=tuple(items))
        for index, items in enumerate(mutable_tracks)
    )
    if not tracks:
        return TrackingResult(
            tracks=(),
            primary_track_id=None,
            primary_coverage=0.0,
            secondary_track_ratio=0.0,
            primary_meets_coverage=False,
            subject_switched=False,
        )
    primary = min(
        tracks,
        key=lambda track: (
            -len(track.observations),
            track.first_frame_index,
            track.track_id,
        ),
    )
    primary_count = len(primary.observations)
    total_valid = len(ordered)
    primary_coverage = primary_count / sampled_frame_count
    secondary_ratio = (total_valid - primary_count) / total_valid
    return TrackingResult(
        tracks=tracks,
        primary_track_id=primary.track_id,
        primary_coverage=primary_coverage,
        secondary_track_ratio=secondary_ratio,
        primary_meets_coverage=primary_coverage >= policy.min_track_coverage,
        subject_switched=secondary_ratio > policy.max_secondary_track_ratio,
    )
