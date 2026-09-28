"""Owned best-effort reference-in-video comparison and trial decisions."""

from __future__ import annotations

import math
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType

from face_algorithm.core.contracts import validate_image_input
from face_algorithm.core.matcher import EmbeddingMatchError, match_embeddings
from face_algorithm.core.pipeline import FacePipeline
from face_algorithm.presence.analyzer import PresenceAnalyzer
from face_algorithm.presence.config import VideoPresencePolicy
from face_algorithm.presence.contracts import PresenceEvidenceFrame, VideoPresenceResult
from face_algorithm.presence.decision import decide_presence
from face_algorithm.presence.sampling import PresenceDecodeRequest, PresenceSampleFrame
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import ModelRegistry, VerifiedModelEvidence
from face_algorithm.schemas import FeatureVersion, ReasonCode, Suggestion
from face_algorithm.video.decoder import (
    DecodedFrame, VideoDeadlineExceeded, VideoDecodeError,
    VideoInputError, VideoMetadata, VideoResourceLimits,
)

__all__ = ["VideoPresenceRuntime", "build_video_presence_runtime"]

_MODEL_ROLES = (
    "face_detector", "face_landmark_primary", "face_landmark_secondary",
    "face_recognizer", "rotation_classifier", "pose_var", "pose_conv",
)


def _finite_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True, slots=True, repr=False)
class _PresenceVerifier:
    analyzer: PresenceAnalyzer
    policy: VideoPresencePolicy
    limits: VideoResourceLimits
    decoder_factory: Callable
    model_versions: Mapping[str, str]
    config_version: str
    policy_fingerprint: str
    deadline_seconds: float

    def verify(self, reference_image: object, video_path: str | os.PathLike[str], *,
               deadline_monotonic: float | None = None) -> VideoPresenceResult:
        started = time.perf_counter()
        deadline = time.monotonic() + self.deadline_seconds if deadline_monotonic is None else deadline_monotonic
        if not _finite_number(deadline):
            raise ValueError("deadline must be a finite absolute monotonic timestamp")

        def check_deadline() -> None:
            if time.monotonic() >= deadline:
                raise VideoDeadlineExceeded(ReasonCode.ALGORITHM_TIMEOUT, "presence scan deadline exceeded")

        check_deadline()
        reasons = [] if self.policy.mode == "configured" else [ReasonCode.THRESHOLD_NOT_CONFIGURED]
        quality_warnings = []
        sampled = decoded = valid = candidates = 0
        base_frames = supplemental_frames = compared = failed_candidates = received = 0
        effective_interval = self.policy.sample_interval_seconds
        reference_valid = False
        candidate_limit = False
        complete = False
        duration = None
        timestamp_source = "unknown"
        evidence: list[PresenceEvidenceFrame] = []
        success = True
        suggestion = Suggestion.REVIEW

        def finish() -> VideoPresenceResult:
            comparison_status = ("FAILED" if not success else "INVALID_REFERENCE" if not reference_valid
                                 else "COMPARED" if compared else "NO_COMPARABLE_FACE")
            best_score = evidence[0].score if evidence else None
            decision, found = decide_presence(success=success, scan_complete=complete,
                comparison_status=comparison_status, best_score=best_score, threshold=self.policy.threshold,
                candidate_limit_hit=candidate_limit, failed_candidates=failed_candidates)
            return VideoPresenceResult(
                success=success, suggestion=suggestion, reason_codes=tuple(dict.fromkeys(reasons)),
                model_versions=self.model_versions, feature_version=FeatureVersion.RECOGNIZER_V1_1024,
                config_version=self.config_version, processing_time_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
                trace_id=uuid.uuid4().hex,
                decision=decision, found=found, comparison_status=comparison_status,
                best_score=best_score,
                best_timestamp_seconds=evidence[0].timestamp_seconds if evidence else None,
                evidence_frames=tuple(evidence), scan_complete=complete,
                sampling_interval_seconds=effective_interval,
                requested_sampling_interval_seconds=self.policy.sample_interval_seconds,
                base_frames=base_frames, supplemental_frames=supplemental_frames,
                compared_candidates=compared, failed_candidates=failed_candidates,
                quality_warnings=tuple(dict.fromkeys(quality_warnings)),
                threshold=self.policy.threshold, threshold_id=self.policy.threshold_id,
                policy_fingerprint=self.policy_fingerprint,
                calibration_status="CONFIGURED_UNVALIDATED" if self.policy.mode == "configured" else "UNCALIBRATED",
                sampled_frames=sampled, decoded_frames=decoded, valid_face_frames=valid,
                candidate_count=candidates, candidate_limit_hit=candidate_limit,
                duration_seconds=duration, timestamp_source=timestamp_source,
            )

        reference = self.analyzer.analyze_reference(reference_image, check_deadline=check_deadline)
        check_deadline()
        candidate_limit = reference.candidate_limit_hit
        quality_warnings.extend(reference.quality_warnings)
        if reference.system_failure or not reference.features:
            reasons.extend(reference.reason_codes)
            if candidate_limit:
                reasons.append(ReasonCode.CANDIDATE_LIMIT_REACHED)
            success = not reference.system_failure
            suggestion = Suggestion.RECAPTURE if success else Suggestion.REVIEW
            return finish()
        reference_feature = reference.features[0]
        reference_valid = True
        stream = None
        metadata = None
        exhausted = False
        previous_index, previous_timestamp = -1, -1.0
        last_analyzed_index = -1
        current_window = -1
        supplement_window = False
        try:
            request = PresenceDecodeRequest(sample_interval_seconds=self.policy.sample_interval_seconds,
                                            max_supplemental_frames=self.policy.max_supplemental_frames)
            stream = self.decoder_factory().open(video_path, request=request, limits=self.limits,
                                                 deadline_monotonic=float(deadline))
            with stream:
                metadata = stream.metadata
                effective_interval = getattr(stream, "sampling_interval_seconds", self.policy.sample_interval_seconds)
                if not _finite_number(effective_interval) or effective_interval < self.policy.sample_interval_seconds:
                    raise VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "invalid effective sampling interval")
                if not isinstance(metadata, VideoMetadata) or (
                    type(metadata.width) is not int or type(metadata.height) is not int
                    or metadata.width <= 0 or metadata.height <= 0
                    or metadata.width * metadata.height > self.limits.max_frame_pixels
                    or not _finite_number(metadata.fps) or not 0 < metadata.fps <= self.limits.max_video_fps
                ):
                    raise VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "invalid presence metadata")
                for frame in stream:
                    check_deadline()
                    if received >= self.policy.max_analyzed_frames:
                        break
                    if not isinstance(frame, (DecodedFrame, PresenceSampleFrame)) or (
                        type(frame.index) is not int or frame.index <= previous_index
                        or not _finite_number(frame.timestamp_seconds) or frame.timestamp_seconds < 0
                        or frame.timestamp_seconds <= previous_timestamp
                        or (received == 0 and (frame.index != 0 or frame.timestamp_seconds != 0.0))
                    ):
                        raise VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "invalid presence frame ordering")
                    shape = validate_image_input(frame.image, self.limits.max_frame_pixels)
                    if shape != (metadata.height, metadata.width):
                        raise VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "invalid presence frame dimensions")
                    previous_index, previous_timestamp = frame.index, float(frame.timestamp_seconds)
                    received += 1
                    decoded = frame.index + 1
                    duration = frame.timestamp_seconds + 1.0 / metadata.fps
                    kind = "base"
                    if isinstance(frame, PresenceSampleFrame):
                        kind = frame.kind
                        if type(frame.window_index) is not int or frame.window_index < 0 or kind not in ("base", "supplemental"):
                            raise VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "invalid sampling frame tag")
                        if kind == "base":
                            if frame.window_index <= current_window:
                                raise VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "invalid base window ordering")
                            current_window = frame.window_index
                        elif frame.window_index != current_window or current_window < 0:
                            raise VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "supplemental frame lacks a base")
                    if kind == "supplemental" and not supplement_window:
                        continue
                    sampled += 1
                    base_frames += int(kind == "base")
                    supplemental_frames += int(kind == "supplemental")
                    analysis = self.analyzer.analyze_frame(frame.image, check_deadline=check_deadline)
                    last_analyzed_index = frame.index
                    check_deadline()
                    candidates += analysis.candidate_count
                    candidate_limit = candidate_limit or analysis.candidate_limit_hit
                    reasons.extend(analysis.reason_codes)
                    quality_warnings.extend(analysis.quality_warnings)
                    failed_candidates += analysis.failed_candidates
                    if kind == "base":
                        supplement_window = bool(not analysis.features or analysis.quality_warnings
                                                 or analysis.failed_candidates or analysis.candidate_limit_hit)
                    if analysis.system_failure:
                        success = False
                        break
                    if analysis.features:
                        if not (len(analysis.features) == len(analysis.feature_candidate_indices)
                                == len(analysis.feature_quality_warnings)):
                            raise ValueError("candidate observation metadata mismatch")
                        scores = [match_embeddings(reference_feature, feature,
                            reference_version=FeatureVersion.RECOGNIZER_V1_1024,
                            probe_version=FeatureVersion.RECOGNIZER_V1_1024).score
                            for feature in analysis.features]
                        check_deadline()
                        compared += len(scores)
                        valid += 1
                        best = min(range(len(scores)), key=lambda i: (-scores[i],
                            len(analysis.feature_quality_warnings[i]), analysis.feature_candidate_indices[i]))
                        evidence.append(PresenceEvidenceFrame(frame_index=frame.index,
                            timestamp_seconds=float(frame.timestamp_seconds), score=scores[best],
                            candidate_index=analysis.feature_candidate_indices[best],
                            quality_warnings=analysis.feature_quality_warnings[best]))
                        evidence.sort(key=lambda item: (-item.score, len(item.quality_warnings), item.timestamp_seconds, item.frame_index))
                        del evidence[self.policy.max_evidence_frames:]
                else:
                    exhausted = True
        except (VideoDeadlineExceeded, VideoInputError):
            raise
        except (VideoDecodeError, EmbeddingMatchError) as error:
            success = False
            reasons.append(error.reason_code)
        except Exception:
            success = False
            reasons.append(ReasonCode.INFERENCE_ERROR)
        check_deadline()
        if stream is not None:
            try:
                summary = stream.summary
                if (
                    type(summary.decoded_frames) is not int or summary.decoded_frames < decoded
                    or type(summary.sampled_frames) is not int or summary.sampled_frames < received
                    or summary.sampled_frames > summary.decoded_frames
                    or type(summary.scan_complete) is not bool
                    or not _finite_number(summary.duration_seconds) or summary.duration_seconds < 0
                    or summary.timestamp_source not in ("pts", "estimated", "mixed", "unknown")
                    or (duration is not None and summary.duration_seconds < duration - 1e-9)
                    or summary.duration_seconds > self.limits.max_duration_seconds
                    or (isinstance(metadata, VideoMetadata) and _finite_number(metadata.fps)
                        and summary.decoded_frames > math.floor(self.limits.max_duration_seconds * metadata.fps + 1e-7))
                    or (summary.scan_complete and (
                        summary.sampled_frames != received or summary.decoded_frames != decoded
                        or summary.timestamp_source == "unknown" or duration is None
                        or not math.isclose(summary.duration_seconds, duration, rel_tol=1e-9)
                    ))
                ):
                    raise ValueError("invalid decoder summary")
                decoded = summary.decoded_frames
                duration = summary.duration_seconds if summary.duration_seconds > 0 else None
                timestamp_source = summary.timestamp_source
                complete = bool(success and exhausted and summary.scan_complete and sampled > 0
                                and summary.sampled_frames == received
                                and last_analyzed_index == summary.decoded_frames - 1)
                if success and exhausted and sampled == 0:
                    success = False
                    reasons.append(ReasonCode.VIDEO_DECODE_FAILED)
            except Exception:
                success = False
                complete = False
                reasons.append(ReasonCode.VIDEO_DECODE_FAILED)
        if candidate_limit:
            reasons.append(ReasonCode.CANDIDATE_LIMIT_REACHED)
        if failed_candidates:
            reasons.append(ReasonCode.CANDIDATE_COMPARISON_INCOMPLETE)
        if success and not complete:
            reasons.append(ReasonCode.VIDEO_SCAN_LIMIT_REACHED)
        return finish()


@dataclass(frozen=True, slots=True, init=False)
class VideoPresenceRuntime:
    """Immutable operation surface owning its isolated models and scan policy."""

    model_versions: Mapping[str, str]
    config_version: str
    _verifier: _PresenceVerifier = field(repr=False)

    def __init__(self) -> None:
        raise TypeError("use build_video_presence_runtime")

    def verify(self, reference_image: object, video_path: str | os.PathLike[str], *,
               deadline_monotonic: float | None = None) -> VideoPresenceResult:
        return self._verifier.verify(reference_image, video_path, deadline_monotonic=deadline_monotonic)


def _bind_video_presence_runtime(*, pipeline: FacePipeline, config: AlgorithmConfig,
                                 decoder_factory: Callable) -> VideoPresenceRuntime:
    """Bind an owned graph; private injection seam for deterministic adapter tests."""

    if not isinstance(config, AlgorithmConfig) or not isinstance(pipeline, FacePipeline):
        raise TypeError("presence requires validated config and an owned FacePipeline")
    config = AlgorithmConfig.model_validate(config.model_dump())
    if not callable(decoder_factory):
        raise TypeError("decoder factory must be callable")
    if pipeline.config_version != config.config_version or pipeline.max_image_pixels != config.resources.max_image_pixels:
        raise ValueError("pipeline configuration differs from presence snapshot")
    evidence, versions = dict(pipeline.model_evidence), dict(pipeline.model_versions)
    if set(evidence) != set(_MODEL_ROLES) or versions != {name: item.version for name, item in evidence.items()}:
        raise ValueError("pipeline model versions do not match verified evidence")
    for name, item in evidence.items():
        expected = getattr(config.models, name)
        if not isinstance(item, VerifiedModelEvidence) or (
            item.name != name or item.version != expected.version or item.sha256 != expected.sha256
            or item.backend != expected.backend or item.providers != expected.providers
            or (name == "face_recognizer" and (item.output_dimension != 1024 or item.feature_version != config.feature_version))
        ):
            raise ValueError("presence model evidence differs from validated configuration")
    if pipeline.embedder.feature_dim != 1024 or pipeline.embedder.feature_version != config.feature_version:
        raise ValueError("presence requires the recognizer_v1_1024 protocol")
    limits = VideoResourceLimits(max_video_bytes=config.resources.max_video_bytes,
        max_duration_seconds=config.resources.max_video_duration_seconds,
        max_video_fps=config.resources.max_video_fps, max_frame_pixels=config.resources.max_image_pixels,
        max_analyzed_frames=config.video_presence.max_analyzed_frames)
    versions = MappingProxyType(versions)
    fingerprint = hashlib.sha256(json.dumps({
        "version": "presence-best-effort-v1", "policy": config.video_presence.model_dump(mode="json"),
        "models": {key: asdict(value) for key, value in sorted(evidence.items())},
        "quality": config.quality_profiles.model_dump(mode="json"),
        "matcher": "l2_dot_v1",
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    verifier = _PresenceVerifier(analyzer=PresenceAnalyzer(pipeline=pipeline, policy=config.video_presence,
        quality_profiles=config.quality_profiles), policy=config.video_presence, limits=limits,
        decoder_factory=decoder_factory, model_versions=versions, config_version=config.config_version,
        policy_fingerprint=fingerprint,
        deadline_seconds=float(config.route_deadlines.video_full_seconds))
    runtime = object.__new__(VideoPresenceRuntime)
    object.__setattr__(runtime, "model_versions", versions)
    object.__setattr__(runtime, "config_version", config.config_version)
    object.__setattr__(runtime, "_verifier", verifier)
    return runtime


def build_video_presence_runtime(*, config: AlgorithmConfig, registry: ModelRegistry) -> VideoPresenceRuntime:
    """Build a separate owned face graph and the trusted sequential decoder."""

    from face_algorithm.core.adapters import build_owned_face_pipeline
    from face_algorithm.video.presence_decoder import OpenCvPresenceDecoder

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be a validated AlgorithmConfig")
    pipeline = build_owned_face_pipeline(config=config, registry=registry)
    return _bind_video_presence_runtime(pipeline=pipeline, config=config, decoder_factory=OpenCvPresenceDecoder)
