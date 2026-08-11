"""Startup-bound quick/full signing-video identity verification."""

from __future__ import annotations

import copy
import math
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

from face_algorithm.core.matcher import MATCHER_VERSION, EmbeddingMatchError, match_embeddings
from face_algorithm.core.pipeline import TrustedFaceAnalysis
from face_algorithm.runtime.calibration import (
    CalibrationState,
    CalibrationStatus,
    TrustedCalibrationKeyStore,
    VideoCalibrationArtifact,
    VideoCalibrationThresholds,
    _is_loader_issued_calibration,
    build_pipeline_fingerprint,
    fingerprint_quality_profile,
    fingerprint_video_policy,
    load_video_calibration,
)
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import ModelRegistry, VerifiedModelEvidence
from face_algorithm.schemas import (
    FEATURE_DIMENSION,
    IMAGE_RECAPTURE_REASON_CODES,
    CalibrationSummary,
    FeatureVersion,
    FrameSummary,
    ReasonCode,
    Suggestion,
    VideoVerificationResult,
)
from face_algorithm.verification.image_verifier import (
    _analysis_consistency_reason,
    _embedding_contract_reason,
)
from face_algorithm.video.aggregator import ScoreAggregate, aggregate_scores, longest_no_face_gap
from face_algorithm.video.decoder import (
    DecodeRequest,
    DecodedFrame,
    OpenCvSubprocessDecoder,
    VideoDecodeError,
    VideoDecoder,
    VideoMetadata,
    VideoResourceLimits,
)
from face_algorithm.video.sampler import full_sample_indices
from face_algorithm.video.tracker import (
    FrameObservation,
    TrackingPolicy,
    TrackingResult,
    build_tracks,
)

__all__ = ["VideoVerificationRuntime", "build_video_verification_runtime"]


@dataclass(frozen=True, slots=True)
class _Resources:
    max_video_bytes: int
    max_video_duration_seconds: float
    max_video_fps: float
    max_image_pixels: int
    max_full_video_frames: int


@dataclass(frozen=True, slots=True)
class _QuickPolicy:
    sample_count: int
    min_valid_frames: int
    include_first_last: bool
    aggregate_percentile: float


@dataclass(frozen=True, slots=True)
class _FullPolicy:
    sample_interval_seconds: float
    min_valid_frames: int
    max_analyzed_frames: int
    track_iou_threshold: float
    track_landmark_distance_threshold: float
    track_embedding_threshold: float
    min_track_coverage: float
    max_secondary_track_ratio: float
    max_no_face_gap_seconds: float
    aggregate_percentile: float


@dataclass(frozen=True, slots=True)
class _CalibrationSnapshot:
    status: CalibrationStatus
    calibration_id: str | None
    artifact_sha256: str | None
    thresholds: VideoCalibrationThresholds | None


@dataclass(slots=True)
class _FrameAccounting:
    requested: int = 0
    decoded: int = 0
    analyzed: int = 0
    valid: int = 0
    no_face: int = 0
    multiple: int = 0
    bad_quality: int = 0
    failed: int = 0
    timestamps: list[float] = field(default_factory=list)
    no_face_flags: list[bool] = field(default_factory=list)
    frame_reasons: list[ReasonCode] = field(default_factory=list)

    def add_reason(self, reason: ReasonCode) -> None:
        if reason not in self.frame_reasons:
            self.frame_reasons.append(reason)

    def public(self) -> FrameSummary:
        return FrameSummary(
            requested_frames=self.requested,
            decoded_frames=self.decoded,
            analyzed_frames=self.analyzed,
            valid_frames=self.valid,
            no_face_frames=self.no_face,
            multiple_face_frames=self.multiple,
            bad_quality_frames=self.bad_quality,
            failed_frames=self.failed,
            first_timestamp_seconds=self.timestamps[0] if self.timestamps else None,
            last_timestamp_seconds=self.timestamps[-1] if self.timestamps else None,
            reason_codes=tuple(self.frame_reasons),
        )


def _copy_input(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.copy()
    try:
        return copy.copy(value)
    except Exception:
        return value


def _valid_video_calibration(
    state: CalibrationState,
    snapshot: _CalibrationSnapshot,
    *,
    pipeline_fingerprint: str,
    policy_fingerprint: str,
    reference_profile_fingerprint: str,
    frame_profile_fingerprint: str,
) -> bool:
    if not _is_loader_issued_calibration(state) or state.status is not snapshot.status:
        return False
    if state.status is CalibrationStatus.UNCALIBRATED:
        return bool(
            snapshot.calibration_id is None
            and snapshot.artifact_sha256 is None
            and snapshot.thresholds is None
            and state.calibration_id is None
            and state.artifact_sha256 is None
            and state.threshold is None
            and state.artifact is None
            and state.video_thresholds is None
        )
    artifact = state.artifact
    thresholds = state.video_thresholds
    return bool(
        isinstance(artifact, VideoCalibrationArtifact)
        and isinstance(thresholds, VideoCalibrationThresholds)
        and thresholds == snapshot.thresholds
        and state.calibration_id == snapshot.calibration_id == artifact.calibration_id
        and state.artifact_sha256 == snapshot.artifact_sha256
        and state.threshold == thresholds.frame_threshold == artifact.frame_threshold
        and artifact.quick_aggregate_threshold
        == thresholds.quick_aggregate_threshold
        and artifact.quick_min_pass_ratio == thresholds.quick_min_pass_ratio
        and artifact.full_aggregate_threshold == thresholds.full_aggregate_threshold
        and artifact.full_min_pass_ratio == thresholds.full_min_pass_ratio
        and artifact.pipeline_fingerprint == pipeline_fingerprint
        and artifact.policy_fingerprint == policy_fingerprint
        and artifact.reference_profile_fingerprint == reference_profile_fingerprint
        and artifact.frame_profile_fingerprint == frame_profile_fingerprint
    )


def _validate_metadata(
    metadata: object,
    resources: _Resources,
) -> ReasonCode | None:
    if not isinstance(metadata, VideoMetadata):
        return ReasonCode.VIDEO_DECODE_FAILED
    if (
        isinstance(metadata.width, bool)
        or not isinstance(metadata.width, int)
        or isinstance(metadata.height, bool)
        or not isinstance(metadata.height, int)
        or metadata.width <= 0
        or metadata.height <= 0
        or isinstance(metadata.total_frames, bool)
        or not isinstance(metadata.total_frames, int)
        or metadata.total_frames <= 0
        or isinstance(metadata.fps, bool)
        or not isinstance(metadata.fps, (int, float))
        or not math.isfinite(float(metadata.fps))
        or metadata.fps <= 0.0
        or isinstance(metadata.duration_seconds, bool)
        or not isinstance(metadata.duration_seconds, (int, float))
        or not math.isfinite(float(metadata.duration_seconds))
        or metadata.duration_seconds <= 0.0
    ):
        return ReasonCode.VIDEO_DECODE_FAILED
    if metadata.width * metadata.height > resources.max_image_pixels:
        return ReasonCode.VIDEO_TOO_LARGE
    if metadata.duration_seconds > resources.max_video_duration_seconds:
        return ReasonCode.VIDEO_TOO_LONG
    if metadata.fps > resources.max_video_fps:
        return ReasonCode.VIDEO_TOO_LARGE
    return None


def _validate_frame(
    frame: object,
    *,
    previous_index: int,
    previous_timestamp: float,
    metadata: VideoMetadata,
    max_image_pixels: int,
) -> ReasonCode | None:
    if not isinstance(frame, DecodedFrame):
        return ReasonCode.VIDEO_DECODE_FAILED
    if (
        isinstance(frame.index, bool)
        or not isinstance(frame.index, int)
        or frame.index <= previous_index
        or isinstance(frame.timestamp_seconds, bool)
        or not isinstance(frame.timestamp_seconds, (int, float))
        or not math.isfinite(float(frame.timestamp_seconds))
        or frame.timestamp_seconds < 0.0
        or frame.timestamp_seconds < previous_timestamp
        or frame.timestamp_seconds > metadata.duration_seconds
        or not isinstance(frame.image, np.ndarray)
        or frame.image.dtype != np.uint8
        or frame.image.ndim != 3
        or frame.image.shape[2] != 3
        or frame.image.shape[:2] != (metadata.height, metadata.width)
        or frame.image.shape[0] * frame.image.shape[1] > max_image_pixels
    ):
        return ReasonCode.VIDEO_DECODE_FAILED
    return None


@dataclass(frozen=True, slots=True)
class _BoundVideoVerifier:
    _analyze_trusted: Callable[[object, Any], TrustedFaceAnalysis]
    _reference_profile: Any
    _frame_profile: Any
    _decoder_factory: Callable[[], VideoDecoder]
    _resources: _Resources
    _quick: _QuickPolicy
    _full: _FullPolicy
    _quick_deadline_seconds: float
    _full_deadline_seconds: float
    _calibration: CalibrationState
    _calibration_snapshot: _CalibrationSnapshot
    _model_versions: Mapping[str, str]
    _config_version: str
    _pipeline_fingerprint: str
    _policy_fingerprint: str
    _reference_profile_fingerprint: str
    _frame_profile_fingerprint: str

    def _calibration_summary(self, mode: Literal["quick", "full"]) -> CalibrationSummary:
        snapshot = self._calibration_snapshot
        thresholds = snapshot.thresholds
        aggregate_threshold: float | None = None
        min_pass_ratio: float | None = None
        if thresholds is not None:
            if mode == "quick":
                aggregate_threshold = thresholds.quick_aggregate_threshold
                min_pass_ratio = thresholds.quick_min_pass_ratio
            else:
                aggregate_threshold = thresholds.full_aggregate_threshold
                min_pass_ratio = thresholds.full_min_pass_ratio
        return CalibrationSummary(
            status=snapshot.status.value,
            calibration_id=snapshot.calibration_id,
            artifact_sha256=snapshot.artifact_sha256,
            frame_threshold=(
                thresholds.frame_threshold if thresholds is not None else None
            ),
            aggregate_threshold=aggregate_threshold,
            min_pass_ratio=min_pass_ratio,
            decision_rule_version=(
                "video_track_aggregate_v1" if thresholds is not None else None
            ),
            pipeline_fingerprint=self._pipeline_fingerprint,
            policy_fingerprint=self._policy_fingerprint,
        )

    def _finish(
        self,
        *,
        started: float,
        mode: Literal["quick", "full"],
        success: bool,
        suggestion: Suggestion,
        reasons: tuple[ReasonCode, ...],
        accounting: _FrameAccounting,
        duration_seconds: float | None = None,
        aggregate: ScoreAggregate | None = None,
        tracking: TrackingResult | None = None,
        no_face_gap: float = 0.0,
    ) -> VideoVerificationResult:
        aggregate = aggregate or ScoreAggregate(0, None, None, None, None)
        tracking = tracking or TrackingResult(
            tracks=(),
            primary_track_id=None,
            primary_coverage=0.0,
            secondary_track_ratio=0.0,
            primary_meets_coverage=False,
            subject_switched=False,
        )
        return VideoVerificationResult(
            success=success,
            suggestion=suggestion,
            reason_codes=reasons,
            model_versions=self._model_versions,
            feature_version=FeatureVersion.RECOGNIZER_V1_1024,
            config_version=self._config_version,
            processing_time_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
            trace_id=uuid.uuid4().hex,
            mode=mode,
            duration_seconds=duration_seconds,
            frames=accounting.public(),
            aggregate_score=aggregate.percentile_score,
            median_score=aggregate.median_score,
            minimum_score=aggregate.minimum_score,
            pass_ratio=aggregate.pass_ratio,
            primary_track_coverage=tracking.primary_coverage,
            secondary_track_ratio=tracking.secondary_track_ratio,
            longest_no_face_gap_seconds=no_face_gap,
            feature_dimension=FEATURE_DIMENSION,
            matcher_version=MATCHER_VERSION,
            calibration=self._calibration_summary(mode),
            liveness_evaluated=False,
        )

    def _analyze(self, image: object, profile: Any) -> tuple[TrustedFaceAnalysis | None, ReasonCode | None]:
        try:
            analysis = self._analyze_trusted(_copy_input(image), profile)
            if not isinstance(analysis, TrustedFaceAnalysis):
                return None, ReasonCode.INFERENCE_ERROR
            consistency = _analysis_consistency_reason(
                analysis,
                model_versions=self._model_versions,
                config_version=self._config_version,
            )
            if consistency is not None:
                return None, consistency
            return analysis, None
        except Exception:
            return None, ReasonCode.INFERENCE_ERROR

    def _absolute_deadline(
        self,
        mode: Literal["quick", "full"],
        supplied: float | None,
    ) -> float | None:
        configured = (
            self._quick_deadline_seconds if mode == "quick" else self._full_deadline_seconds
        )
        local = time.monotonic() + configured
        if supplied is None:
            return local
        if (
            isinstance(supplied, bool)
            or not isinstance(supplied, (int, float))
            or not math.isfinite(float(supplied))
        ):
            return None
        return min(local, float(supplied))

    def _preflight(self, video_path: object) -> tuple[Path | None, ReasonCode | None]:
        try:
            path = Path(video_path)
            stat = path.stat()
        except (TypeError, OSError):
            return None, ReasonCode.VIDEO_DECODE_FAILED
        if not path.is_file() or stat.st_size <= 0:
            return None, ReasonCode.VIDEO_DECODE_FAILED
        if stat.st_size > self._resources.max_video_bytes:
            return None, ReasonCode.VIDEO_TOO_LARGE
        return path, None

    def verify(
        self,
        reference_image: object,
        video_path: str | os.PathLike[str],
        *,
        mode: Literal["quick", "full"],
        deadline_monotonic: float | None,
    ) -> VideoVerificationResult:
        started = time.perf_counter()
        accounting = _FrameAccounting()
        deadline = self._absolute_deadline(mode, deadline_monotonic)
        if deadline is None:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
                accounting=accounting,
            )

        path, preflight_reason = self._preflight(video_path)
        if preflight_reason is not None:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(preflight_reason,),
                accounting=accounting,
            )
        assert path is not None
        if time.monotonic() >= deadline:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.ALGORITHM_TIMEOUT,),
                accounting=accounting,
            )
        if not _valid_video_calibration(
            self._calibration,
            self._calibration_snapshot,
            pipeline_fingerprint=self._pipeline_fingerprint,
            policy_fingerprint=self._policy_fingerprint,
            reference_profile_fingerprint=self._reference_profile_fingerprint,
            frame_profile_fingerprint=self._frame_profile_fingerprint,
        ):
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
                accounting=accounting,
            )

        reference, reference_error = self._analyze(reference_image, self._reference_profile)
        if reference_error is not None or reference is None:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(reference_error or ReasonCode.INFERENCE_ERROR,),
                accounting=accounting,
            )
        reference_reasons = reference.public.reason_codes
        if reference_reasons:
            correctable = all(
                reason in IMAGE_RECAPTURE_REASON_CODES for reason in reference_reasons
            )
            return self._finish(
                started=started,
                mode=mode,
                success=correctable,
                suggestion=Suggestion.RECAPTURE if correctable else Suggestion.REVIEW,
                reasons=reference_reasons,
                accounting=accounting,
            )
        reference_contract = _embedding_contract_reason(reference)
        if reference_contract is not None:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(reference_contract,),
                accounting=accounting,
            )

        request = DecodeRequest(
            mode=mode,
            quick_sample_count=self._quick.sample_count,
            include_first_last=self._quick.include_first_last,
            full_interval_seconds=self._full.sample_interval_seconds,
        )
        limits = VideoResourceLimits(
            max_video_bytes=self._resources.max_video_bytes,
            max_duration_seconds=self._resources.max_video_duration_seconds,
            max_video_fps=self._resources.max_video_fps,
            max_frame_pixels=self._resources.max_image_pixels,
            max_analyzed_frames=min(
                self._resources.max_full_video_frames,
                self._full.max_analyzed_frames,
            ),
        )
        observations: list[FrameObservation] = []
        duration: float | None = None
        previous_index = -1
        previous_timestamp = -1.0
        try:
            decoder = self._decoder_factory()
            if not isinstance(decoder, VideoDecoder):
                raise VideoDecodeError(
                    ReasonCode.VIDEO_DECODE_FAILED,
                    "decoder factory returned an invalid decoder",
                )
            with decoder.open(
                path,
                request=request,
                limits=limits,
                deadline_monotonic=deadline,
            ) as stream:
                metadata_reason = _validate_metadata(stream.metadata, self._resources)
                if metadata_reason is not None:
                    return self._finish(
                        started=started,
                        mode=mode,
                        success=False,
                        suggestion=Suggestion.REVIEW,
                        reasons=(metadata_reason,),
                        accounting=accounting,
                    )
                metadata = stream.metadata
                duration = float(metadata.duration_seconds)
                if mode == "quick":
                    accounting.requested = min(
                        metadata.total_frames,
                        self._quick.sample_count,
                    )
                else:
                    accounting.requested = len(
                        full_sample_indices(
                            total_frames=metadata.total_frames,
                            fps=float(metadata.fps),
                            interval_seconds=self._full.sample_interval_seconds,
                            max_frames=min(
                                self._resources.max_full_video_frames,
                                self._full.max_analyzed_frames,
                            ),
                        )
                    )

                for frame in stream:
                    if time.monotonic() >= deadline:
                        raise VideoDecodeError(
                            ReasonCode.ALGORITHM_TIMEOUT,
                            "video verification deadline exceeded",
                        )
                    if accounting.decoded >= accounting.requested:
                        raise VideoDecodeError(
                            ReasonCode.VIDEO_DECODE_FAILED,
                            "decoder exceeded the requested frame count",
                        )
                    frame_reason = _validate_frame(
                        frame,
                        previous_index=previous_index,
                        previous_timestamp=previous_timestamp,
                        metadata=metadata,
                        max_image_pixels=self._resources.max_image_pixels,
                    )
                    if frame_reason is not None:
                        raise VideoDecodeError(frame_reason, "decoded frame is invalid")
                    previous_index = frame.index
                    previous_timestamp = float(frame.timestamp_seconds)
                    accounting.decoded += 1
                    accounting.analyzed += 1
                    accounting.timestamps.append(previous_timestamp)

                    analysis, analysis_error = self._analyze(
                        frame.image,
                        self._frame_profile,
                    )
                    if analysis_error is not None or analysis is None:
                        accounting.failed += 1
                        accounting.no_face_flags.append(False)
                        accounting.add_reason(
                            analysis_error or ReasonCode.INFERENCE_ERROR
                        )
                        continue
                    reasons = analysis.public.reason_codes
                    if ReasonCode.MULTIPLE_FACES in reasons:
                        accounting.multiple += 1
                        accounting.no_face_flags.append(False)
                        accounting.add_reason(ReasonCode.MULTIPLE_SUBJECTS)
                        continue
                    if ReasonCode.NO_FACE in reasons:
                        accounting.no_face += 1
                        accounting.no_face_flags.append(True)
                        accounting.add_reason(ReasonCode.NO_FACE)
                        continue
                    if reasons:
                        hard = tuple(
                            reason
                            for reason in reasons
                            if reason not in IMAGE_RECAPTURE_REASON_CODES
                        )
                        if hard:
                            accounting.failed += 1
                            for reason in hard:
                                accounting.add_reason(reason)
                        else:
                            accounting.bad_quality += 1
                            for reason in reasons:
                                accounting.add_reason(reason)
                        accounting.no_face_flags.append(False)
                        continue
                    embedding_reason = _embedding_contract_reason(analysis)
                    if embedding_reason is not None:
                        accounting.failed += 1
                        accounting.no_face_flags.append(False)
                        accounting.add_reason(embedding_reason)
                        continue
                    if analysis.public.box is None or analysis.public.landmarks is None:
                        accounting.failed += 1
                        accounting.no_face_flags.append(False)
                        accounting.add_reason(ReasonCode.INFERENCE_ERROR)
                        continue
                    try:
                        assert reference.embedding is not None
                        assert analysis.embedding is not None
                        match = match_embeddings(
                            reference.embedding,
                            analysis.embedding,
                            reference_version=reference.public.feature_version,
                            probe_version=analysis.public.feature_version,
                        )
                        landmarks = np.asarray(
                            [
                                (point.x, point.y)
                                for point in analysis.public.landmarks.points
                            ],
                            dtype=np.float32,
                        )
                        observation = FrameObservation(
                            frame_index=frame.index,
                            timestamp_seconds=previous_timestamp,
                            box=analysis.public.box,
                            landmarks=landmarks,
                            embedding=analysis.embedding,
                            similarity_to_reference=match.score,
                        )
                    except EmbeddingMatchError as error:
                        accounting.failed += 1
                        accounting.no_face_flags.append(False)
                        accounting.add_reason(error.reason_code)
                        continue
                    except Exception:
                        accounting.failed += 1
                        accounting.no_face_flags.append(False)
                        accounting.add_reason(ReasonCode.INFERENCE_ERROR)
                        continue
                    observations.append(observation)
                    accounting.valid += 1
                    accounting.no_face_flags.append(False)
        except VideoDecodeError as error:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(error.reason_code,),
                accounting=accounting,
                duration_seconds=duration,
            )
        except Exception:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.VIDEO_DECODE_FAILED,),
                accounting=accounting,
                duration_seconds=duration,
            )

        if accounting.decoded != accounting.requested:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.VIDEO_DECODE_FAILED,),
                accounting=accounting,
                duration_seconds=duration,
            )

        tracking_policy = TrackingPolicy(
            iou_threshold=self._full.track_iou_threshold,
            landmark_distance_threshold=self._full.track_landmark_distance_threshold,
            embedding_threshold=self._full.track_embedding_threshold,
            min_track_coverage=(
                self._full.min_track_coverage if mode == "full" else 0.0
            ),
            max_secondary_track_ratio=self._full.max_secondary_track_ratio,
        )
        try:
            tracking = build_tracks(
                tuple(observations),
                sampled_frame_count=max(accounting.analyzed, 1),
                policy=tracking_policy,
            )
        except Exception:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
                accounting=accounting,
                duration_seconds=duration,
            )
        primary = tracking.primary_track
        primary_scores = (
            tuple(item.similarity_to_reference for item in primary.observations)
            if primary is not None
            else ()
        )
        thresholds = self._calibration_snapshot.thresholds
        percentile = (
            self._quick.aggregate_percentile
            if mode == "quick"
            else self._full.aggregate_percentile
        )
        try:
            aggregate = aggregate_scores(
                primary_scores,
                frame_threshold=(
                    thresholds.frame_threshold if thresholds is not None else None
                ),
                percentile=percentile,
            )
        except Exception:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
                accounting=accounting,
                duration_seconds=duration,
                tracking=tracking,
            )
        assert duration is not None
        no_face_gap = longest_no_face_gap(
            timestamps=tuple(accounting.timestamps),
            no_face=tuple(accounting.no_face_flags),
            duration_seconds=duration,
        )

        if accounting.multiple:
            return self._finish(
                started=started,
                mode=mode,
                success=True,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.MULTIPLE_SUBJECTS,),
                accounting=accounting,
                duration_seconds=duration,
                aggregate=aggregate,
                tracking=tracking,
                no_face_gap=no_face_gap,
            )
        hard_frame_reasons = tuple(
            reason
            for reason in accounting.frame_reasons
            if reason not in IMAGE_RECAPTURE_REASON_CODES
            and reason not in (ReasonCode.MULTIPLE_SUBJECTS,)
        )
        if hard_frame_reasons:
            return self._finish(
                started=started,
                mode=mode,
                success=False,
                suggestion=Suggestion.REVIEW,
                reasons=hard_frame_reasons,
                accounting=accounting,
                duration_seconds=duration,
                aggregate=aggregate,
                tracking=tracking,
                no_face_gap=no_face_gap,
            )
        if tracking.subject_switched:
            return self._finish(
                started=started,
                mode=mode,
                success=True,
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.SUBJECT_SWITCHED,),
                accounting=accounting,
                duration_seconds=duration,
                aggregate=aggregate,
                tracking=tracking,
                no_face_gap=no_face_gap,
            )

        minimum_valid = (
            self._quick.min_valid_frames if mode == "quick" else self._full.min_valid_frames
        )
        correctable: list[ReasonCode] = []
        insufficient = aggregate.valid_count < minimum_valid or (
            mode == "full" and not tracking.primary_meets_coverage
        )
        if insufficient:
            correctable.append(ReasonCode.INSUFFICIENT_VALID_FRAMES)
            for reason in accounting.frame_reasons:
                if reason in IMAGE_RECAPTURE_REASON_CODES and reason not in correctable:
                    correctable.append(reason)
        if mode == "full" and no_face_gap > self._full.max_no_face_gap_seconds:
            correctable.append(ReasonCode.NO_FACE)
        if correctable:
            ordered = []
            if ReasonCode.INSUFFICIENT_VALID_FRAMES in correctable:
                ordered.append(ReasonCode.INSUFFICIENT_VALID_FRAMES)
            ordered.extend(reason for reason in correctable if reason not in ordered)
            return self._finish(
                started=started,
                mode=mode,
                success=True,
                suggestion=Suggestion.RECAPTURE,
                reasons=tuple(ordered),
                accounting=accounting,
                duration_seconds=duration,
                aggregate=aggregate,
                tracking=tracking,
                no_face_gap=no_face_gap,
            )

        if thresholds is None:
            return self._finish(
                started=started,
                mode=mode,
                success=True,
                suggestion=Suggestion.REVIEW,
                reasons=(
                    ReasonCode.CALIBRATION_REQUIRED,
                    ReasonCode.THRESHOLD_UNCALIBRATED,
                ),
                accounting=accounting,
                duration_seconds=duration,
                aggregate=aggregate,
                tracking=tracking,
                no_face_gap=no_face_gap,
            )
        aggregate_threshold = (
            thresholds.quick_aggregate_threshold
            if mode == "quick"
            else thresholds.full_aggregate_threshold
        )
        min_pass_ratio = (
            thresholds.quick_min_pass_ratio
            if mode == "quick"
            else thresholds.full_min_pass_ratio
        )
        if (
            aggregate.percentile_score is not None
            and aggregate.pass_ratio is not None
            and aggregate.percentile_score >= aggregate_threshold
            and aggregate.pass_ratio >= min_pass_ratio
        ):
            return self._finish(
                started=started,
                mode=mode,
                success=True,
                suggestion=Suggestion.PASS,
                reasons=(),
                accounting=accounting,
                duration_seconds=duration,
                aggregate=aggregate,
                tracking=tracking,
                no_face_gap=no_face_gap,
            )
        return self._finish(
            started=started,
            mode=mode,
            success=True,
            suggestion=Suggestion.REVIEW,
            reasons=(ReasonCode.IDENTITY_UNCERTAIN,),
            accounting=accounting,
            duration_seconds=duration,
            aggregate=aggregate,
            tracking=tracking,
            no_face_gap=no_face_gap,
        )


@dataclass(frozen=True, slots=True, init=False)
class VideoVerificationRuntime:
    """Immutable video runtime owning its face graph and decoder factory."""

    model_versions: Mapping[str, str]
    config_version: str
    pipeline_fingerprint: str
    policy_fingerprint: str
    _verifier: _BoundVideoVerifier = field(repr=False)

    def __init__(self) -> None:
        raise TypeError("use build_video_verification_runtime")

    @classmethod
    def _from_builder(
        cls,
        *,
        model_versions: Mapping[str, str],
        config_version: str,
        pipeline_fingerprint: str,
        policy_fingerprint: str,
        verifier: _BoundVideoVerifier,
    ) -> "VideoVerificationRuntime":
        instance = object.__new__(cls)
        object.__setattr__(instance, "model_versions", MappingProxyType(dict(model_versions)))
        object.__setattr__(instance, "config_version", config_version)
        object.__setattr__(instance, "pipeline_fingerprint", pipeline_fingerprint)
        object.__setattr__(instance, "policy_fingerprint", policy_fingerprint)
        object.__setattr__(instance, "_verifier", verifier)
        return instance

    def quick_verify(
        self,
        reference_image: object,
        video_path: str | os.PathLike[str],
        *,
        deadline_monotonic: float | None = None,
    ) -> VideoVerificationResult:
        return self._verifier.verify(
            reference_image,
            video_path,
            mode="quick",
            deadline_monotonic=deadline_monotonic,
        )

    def full_verify(
        self,
        reference_image: object,
        video_path: str | os.PathLike[str],
        *,
        deadline_monotonic: float | None = None,
    ) -> VideoVerificationResult:
        return self._verifier.verify(
            reference_image,
            video_path,
            mode="full",
            deadline_monotonic=deadline_monotonic,
        )

    quick_verify_video = quick_verify
    verify_video = full_verify


def _bind_video_verification_runtime(
    *,
    pipeline: Any,
    config: AlgorithmConfig,
    key_store: TrustedCalibrationKeyStore | None,
    decoder_factory: Callable[[], VideoDecoder] | None = None,
) -> VideoVerificationRuntime:
    """Bind an owned pipeline; this private seam exists for deterministic tests."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be a validated AlgorithmConfig")
    policy = config.video_verification.model_copy(deep=True)
    quality_profiles = config.quality_profiles.model_copy(deep=True)
    reference_profile = getattr(
        quality_profiles,
        policy.reference_quality_profile,
    )
    frame_profile = getattr(quality_profiles, policy.frame_quality_profile)
    if not reference_profile.require_single_face or not frame_profile.require_single_face:
        raise ValueError("video verification profiles must require exactly one face")
    raw_evidence = getattr(pipeline, "model_evidence", None)
    raw_versions = getattr(pipeline, "model_versions", None)
    if not isinstance(raw_evidence, Mapping) or not isinstance(raw_versions, Mapping):
        raise TypeError("pipeline must contain verified evidence and model versions")
    evidence: dict[str, VerifiedModelEvidence] = dict(raw_evidence)
    model_versions = dict(raw_versions)
    expected_versions = {name: item.version for name, item in evidence.items()}
    if model_versions != expected_versions:
        raise ValueError("pipeline model versions do not match verified evidence")
    config_version = getattr(pipeline, "config_version", None)
    if config_version != config.config_version:
        raise ValueError("pipeline config version does not match runtime config")
    analyze_trusted = getattr(pipeline, "analyze_trusted", None)
    if not callable(analyze_trusted):
        raise TypeError("pipeline must provide trusted face analysis")
    reference_fingerprint = fingerprint_quality_profile(
        reference_profile,
        role="reference",
    )
    frame_fingerprint = fingerprint_quality_profile(frame_profile, role="probe")
    pipeline_fingerprint = build_pipeline_fingerprint(
        evidence,
        reference_profile_fingerprint=reference_fingerprint,
        probe_profile_fingerprint=frame_fingerprint,
        scenario="contract_video_v1",
        decision_rule_version="video_track_aggregate_v1",
    )
    policy_fingerprint = fingerprint_video_policy(policy)
    calibration = load_video_calibration(
        policy,
        key_store=key_store,
        model_evidence=evidence,
        quality_profiles=quality_profiles,
    )
    calibration_snapshot = _CalibrationSnapshot(
        status=calibration.status,
        calibration_id=calibration.calibration_id,
        artifact_sha256=calibration.artifact_sha256,
        thresholds=(
            None
            if calibration.video_thresholds is None
            else VideoCalibrationThresholds(
                frame_threshold=calibration.video_thresholds.frame_threshold,
                quick_aggregate_threshold=(
                    calibration.video_thresholds.quick_aggregate_threshold
                ),
                quick_min_pass_ratio=calibration.video_thresholds.quick_min_pass_ratio,
                full_aggregate_threshold=(
                    calibration.video_thresholds.full_aggregate_threshold
                ),
                full_min_pass_ratio=calibration.video_thresholds.full_min_pass_ratio,
            )
        ),
    )
    factory = decoder_factory or OpenCvSubprocessDecoder
    if not callable(factory):
        raise TypeError("decoder_factory must be callable")

    resources = _Resources(
        max_video_bytes=config.resources.max_video_bytes,
        max_video_duration_seconds=config.resources.max_video_duration_seconds,
        max_video_fps=config.resources.max_video_fps,
        max_image_pixels=config.resources.max_image_pixels,
        max_full_video_frames=config.resources.max_full_video_frames,
    )
    quick = _QuickPolicy(
        sample_count=policy.quick.sample_count,
        min_valid_frames=policy.quick.min_valid_frames,
        include_first_last=policy.quick.include_first_last,
        aggregate_percentile=policy.quick.aggregate_percentile,
    )
    full = _FullPolicy(
        sample_interval_seconds=policy.full.sample_interval_seconds,
        min_valid_frames=policy.full.min_valid_frames,
        max_analyzed_frames=policy.full.max_analyzed_frames,
        track_iou_threshold=policy.full.track_iou_threshold,
        track_landmark_distance_threshold=policy.full.track_landmark_distance_threshold,
        track_embedding_threshold=policy.full.track_embedding_threshold,
        min_track_coverage=policy.full.min_track_coverage,
        max_secondary_track_ratio=policy.full.max_secondary_track_ratio,
        max_no_face_gap_seconds=policy.full.max_no_face_gap_seconds,
        aggregate_percentile=policy.full.aggregate_percentile,
    )
    versions_snapshot: Mapping[str, str] = MappingProxyType(model_versions)
    verifier = _BoundVideoVerifier(
        _analyze_trusted=analyze_trusted,
        _reference_profile=reference_profile,
        _frame_profile=frame_profile,
        _decoder_factory=factory,
        _resources=resources,
        _quick=quick,
        _full=full,
        _quick_deadline_seconds=config.route_deadlines.video_quick_seconds,
        _full_deadline_seconds=config.route_deadlines.video_full_seconds,
        _calibration=calibration,
        _calibration_snapshot=calibration_snapshot,
        _model_versions=versions_snapshot,
        _config_version=config_version,
        _pipeline_fingerprint=pipeline_fingerprint,
        _policy_fingerprint=policy_fingerprint,
        _reference_profile_fingerprint=reference_fingerprint,
        _frame_profile_fingerprint=frame_fingerprint,
    )
    return VideoVerificationRuntime._from_builder(
        model_versions=versions_snapshot,
        config_version=config_version,
        pipeline_fingerprint=pipeline_fingerprint,
        policy_fingerprint=policy_fingerprint,
        verifier=verifier,
    )


def build_video_verification_runtime(
    *,
    registry: ModelRegistry,
    config: AlgorithmConfig,
    key_store: TrustedCalibrationKeyStore | None,
) -> VideoVerificationRuntime:
    """Build and own an isolated face execution graph for signing videos."""

    from face_algorithm.core.adapters import build_owned_face_pipeline

    pipeline = build_owned_face_pipeline(config=config, registry=registry)
    return _bind_video_verification_runtime(
        pipeline=pipeline,
        config=config,
        key_store=key_store,
        decoder_factory=OpenCvSubprocessDecoder,
    )
