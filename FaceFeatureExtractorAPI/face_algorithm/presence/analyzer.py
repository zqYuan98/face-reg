"""Best-effort candidate analysis, isolated from strict identity capabilities."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from face_algorithm.core.contracts import (
    FeatureContractError, FeatureVersionMismatchError, ImageTooLargeError,
    InvalidImageError, validate_image_input,
)
from face_algorithm.core.pipeline import FacePipeline
from face_algorithm.presence.config import VideoPresencePolicy
from face_algorithm.presence.quality import alignment_issue, evaluate_quality_warnings
from face_algorithm.schemas import FEATURE_DIMENSION, FaceBox, FeatureVersion, Pose, ReasonCode
from face_algorithm.video.decoder import VideoDeadlineExceeded

__all__ = ["PresenceAnalysis", "PresenceAnalyzer"]


@dataclass(frozen=True, slots=True, repr=False)
class PresenceAnalysis:
    """Private observations; feature metadata retains original detector indices."""

    features: tuple[np.ndarray, ...] = field(repr=False, compare=False, default=())
    candidate_count: int = 0
    candidate_limit_hit: bool = False
    reason_codes: tuple[ReasonCode, ...] = ()
    system_failure: bool = False
    feature_candidate_indices: tuple[int, ...] = ()
    feature_quality_warnings: tuple[tuple[ReasonCode, ...], ...] = ()
    quality_warnings: tuple[ReasonCode, ...] = ()
    failed_candidates: int = 0


class _CandidateFeatureInvalid(ValueError):
    """A correctly shaped recognizer output is numerically unusable."""


def _noop() -> None:
    pass


def _visible_box(value: object, image_shape: tuple[int, ...]) -> FaceBox | None:
    if not isinstance(value, FaceBox):
        raise TypeError("detector candidates must be FaceBox values")
    height, width = image_shape[:2]
    x1, y1 = max(0.0, value.x1), max(0.0, value.y1)
    x2, y2 = min(float(width), value.x2), min(float(height), value.y2)
    if x2 <= x1 or y2 <= y1:
        return None
    # Landmark5er.crop_face rounds endpoints before slicing, including Python's
    # ties-to-even rule. Positive float area need not contain a model input pixel.
    if round(x2) <= round(x1) or round(y2) <= round(y1):
        return None
    return FaceBox(x1=x1, y1=y1, x2=x2, y2=y2, confidence=value.confidence)


def _normalized_feature(embedder: Any, aligned: np.ndarray) -> np.ndarray:
    """Separate numeric candidate failures from recognizer protocol failures."""
    if (getattr(embedder, "feature_version", None) != FeatureVersion.RECOGNIZER_V1_1024.value
            or getattr(embedder, "feature_dim", None) != FEATURE_DIMENSION):
        raise FeatureVersionMismatchError("recognizer metadata is not v1/1024")
    # Session/unknown errors intentionally propagate as global failures.
    raw = embedder.extract(aligned)
    if not isinstance(raw, np.ndarray):
        raise FeatureContractError("recognizer output must be an ndarray")
    if raw.shape != (FEATURE_DIMENSION,):
        if raw.size == 512:
            raise FeatureVersionMismatchError("recognizer output dimension is 512")
        raise FeatureContractError("recognizer output shape must be (1024,)")
    if raw.dtype != np.float32:
        raise FeatureContractError("recognizer output dtype must be float32")
    values = raw.astype(np.float64, copy=True)
    if not np.all(np.isfinite(values)):
        raise _CandidateFeatureInvalid("nonfinite candidate feature")
    norm = float(np.linalg.norm(values))
    if not np.isfinite(norm) or norm <= 0.0:
        raise _CandidateFeatureInvalid("zero or nonfinite candidate norm")
    feature = (values / norm).astype(np.float32)
    normalized_norm = float(np.linalg.norm(feature.astype(np.float64)))
    if (not np.all(np.isfinite(feature)) or not np.isfinite(normalized_norm)
            or not np.isclose(normalized_norm, 1.0, rtol=1e-5, atol=1e-6)):
        raise _CandidateFeatureInvalid("candidate feature cannot be normalized")
    feature.setflags(write=False)
    return feature


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PresenceAnalyzer:
    """Use owned quality profiles without imposing their soft rejection gates."""

    _pipeline: FacePipeline = field(repr=False)
    _policy: VideoPresencePolicy
    _reference_profile: Any = field(repr=False)
    _frame_profile: Any = field(repr=False)

    def __init__(self, *, pipeline: FacePipeline, policy: VideoPresencePolicy, quality_profiles: Any):
        if not isinstance(pipeline, FacePipeline):
            raise TypeError("presence analysis requires an immutable FacePipeline")
        policy = VideoPresencePolicy.model_validate(policy.model_dump())
        profiles = quality_profiles.model_copy(deep=True)
        reference = profiles.contract_reference.model_copy(update={
            "face_selection": profiles.portrait.face_selection.model_copy(update={
                "max_face_candidates": min(policy.max_face_candidates, profiles.portrait.face_selection.max_face_candidates),
            }),
            "resolution": profiles.contract_reference.resolution.model_copy(update={
                "width": policy.reference_min_face_size, "height": policy.reference_min_face_size,
            }),
        })
        frame = profiles.contract_video_frame.model_copy(update={
            "resolution": profiles.contract_video_frame.resolution.model_copy(update={
                "width": policy.video_min_face_size, "height": policy.video_min_face_size,
            }),
        })
        object.__setattr__(self, "_pipeline", pipeline)
        object.__setattr__(self, "_policy", policy)
        object.__setattr__(self, "_reference_profile", reference)
        object.__setattr__(self, "_frame_profile", frame)

    def analyze_reference(self, image: object, *, check_deadline: Callable[[], None] = _noop) -> PresenceAnalysis:
        return self._analyze(image, reference=True, check_deadline=check_deadline)

    def analyze_frame(self, image: object, *, check_deadline: Callable[[], None] = _noop) -> PresenceAnalysis:
        return self._analyze(image, reference=False, check_deadline=check_deadline)

    def _landmarks(self, image: np.ndarray, box: FaceBox, check_deadline: Callable[[], None]):
        points = np.asarray(self._pipeline.landmarker.detect(image, box))
        check_deadline()
        issue = alignment_issue(points, image.shape)
        points = points.copy()
        points.setflags(write=False)
        return points, issue

    def _reference_target(self, image: np.ndarray, detected: Sequence, check_deadline: Callable[[], None]):
        """Dominant-primary intent, with only presence technical geometry checks."""
        selection = self._reference_profile.face_selection
        candidates = []
        reasons: list[ReasonCode] = []
        failures = 0
        for index, raw in enumerate(detected):
            box = _visible_box(raw, image.shape)
            if box is None:
                failures += 1
                reasons.append(ReasonCode.CANDIDATE_ALIGNMENT_INVALID)
            else:
                candidates.append((index, box))
        height, width = image.shape[:2]
        candidates.sort(key=lambda item: (
            -item[1].width * item[1].height, -item[1].confidence,
            (((item[1].x1 + item[1].x2) / (2 * width) - 0.5) ** 2
             + ((item[1].y1 + item[1].y2) / (2 * height) - 0.5) ** 2),
            item[1].x1, item[1].y1, item[1].x2, item[1].y2,
        ))
        assessments = {}
        primary = None
        for index, box in candidates:
            check_deadline()
            points, issue = self._landmarks(image, box, check_deadline)
            assessments[index] = (points, issue)
            if issue is None:
                primary = (index, box, points)
                break
            failures += 1
            reasons.append(issue)
        if primary is None:
            return None, tuple(dict.fromkeys(reasons)) or (ReasonCode.NO_FACE,), failures
        primary_index, primary_box, _ = primary
        for index, box in candidates:
            if index == primary_index:
                continue
            ratio = box.width * box.height / (primary_box.width * primary_box.height)
            if ratio < selection.ignore_secondary_area_ratio - 1e-9:
                continue
            if index not in assessments:
                check_deadline()
                assessments[index] = self._landmarks(image, box, check_deadline)
                if assessments[index][1] is not None:
                    failures += 1
                    reasons.append(assessments[index][1])
            valid = assessments[index][1] is None
            confident = box.confidence >= selection.secondary_min_confidence
            ambiguous = (valid and confident) if ratio < selection.ambiguous_secondary_area_ratio - 1e-9 else (valid or confident)
            if ambiguous:
                return None, (ReasonCode.MULTIPLE_FACES,), failures
        return primary, tuple(dict.fromkeys(reasons)), failures

    def _analyze(self, image: object, *, reference: bool, check_deadline: Callable[[], None]) -> PresenceAnalysis:
        pipeline = self._pipeline
        check_deadline()
        try:
            original_shape = validate_image_input(image, pipeline.max_image_pixels)
        except InvalidImageError:
            return PresenceAnalysis(reason_codes=(ReasonCode.INVALID_IMAGE,), system_failure=True)
        except ImageTooLargeError:
            return PresenceAnalysis(reason_codes=(ReasonCode.IMAGE_TOO_LARGE,), system_failure=True)
        features: list[np.ndarray] = []
        indices: list[int] = []
        feature_warnings: list[tuple[ReasonCode, ...]] = []
        warnings: list[ReasonCode] = []
        reasons: list[ReasonCode] = []
        candidate_count = failed_candidates = 0
        limit_hit = False

        def finish(*, system_reason: ReasonCode | None = None):
            return PresenceAnalysis(
                features=tuple(features), feature_candidate_indices=tuple(indices),
                feature_quality_warnings=tuple(feature_warnings), quality_warnings=tuple(dict.fromkeys(warnings)),
                candidate_count=candidate_count, failed_candidates=failed_candidates, candidate_limit_hit=limit_hit,
                reason_codes=tuple(dict.fromkeys((*reasons, *((system_reason,) if system_reason else ())))),
                system_failure=system_reason is not None,
            )

        try:
            rotation = pipeline.rotator.correct(image.copy())
            check_deadline()
            corrected = rotation.corrected_image
            corrected_shape = validate_image_input(corrected, pipeline.max_image_pixels)
            turns = rotation.quarter_turns
            if isinstance(turns, bool) or not isinstance(turns, int) or turns not in range(4):
                raise ValueError("invalid rotation")
            if corrected_shape != (original_shape if turns % 2 == 0 else original_shape[::-1]):
                raise ValueError("rotation dimensions changed")
            detected = pipeline.detector.detect(corrected)
            check_deadline()
            if not isinstance(detected, Sequence):
                raise TypeError("detector output must be a bounded sequence")
            saturated = getattr(detected, "saturated", False)
            raw_count = getattr(detected, "raw_candidate_count", len(detected))
            if type(saturated) is not bool or type(raw_count) is not int or raw_count < len(detected):
                raise ValueError("invalid detector accounting")
            limit_hit = saturated or len(detected) > self._policy.max_face_candidates
            profile = self._reference_profile if reference else self._frame_profile
            if reference:
                candidate_count = min(len(detected), self._policy.max_face_candidates)
                if limit_hit or len(detected) > profile.face_selection.max_face_candidates:
                    reasons.append(ReasonCode.MULTIPLE_FACES)
                    return finish()
                target, reference_reasons, failed_candidates = self._reference_target(corrected, detected, check_deadline)
                reasons.extend(reference_reasons)
                if target is None:
                    return finish()
                candidates = (target,)
            else:
                candidates = ((index, raw, None) for index, raw in enumerate(detected[:self._policy.max_face_candidates]))
            for index, box, landmarks in candidates:
                check_deadline()
                if not reference:
                    candidate_count += 1
                    box = _visible_box(box, corrected.shape)
                    if box is None:
                        reasons.append(ReasonCode.CANDIDATE_ALIGNMENT_INVALID)
                        failed_candidates += 1
                        continue
                    landmarks, issue = self._landmarks(corrected, box, check_deadline)
                    if issue is not None:
                        reasons.append(issue)
                        failed_candidates += 1
                        continue
                feature, candidate_warnings, issue = self._assess(corrected, box, landmarks, profile, check_deadline)
                warnings.extend(candidate_warnings)
                if issue is not None:
                    reasons.append(issue)
                    failed_candidates += 1
                if feature is not None:
                    features.append(feature)
                    indices.append(index)
                    feature_warnings.append(candidate_warnings)
            if not detected:
                reasons.append(ReasonCode.NO_FACE)
            return finish()
        except VideoDeadlineExceeded:
            raise
        except FeatureVersionMismatchError:
            return finish(system_reason=ReasonCode.FEATURE_VERSION_MISMATCH)
        except FeatureContractError:
            return finish(system_reason=ReasonCode.FEATURE_FAILED)
        except Exception:
            # Unknown errors are global, never recast as low quality/local failure.
            return finish(system_reason=ReasonCode.INFERENCE_ERROR)

    def _assess(self, image: np.ndarray, box: FaceBox, landmarks: np.ndarray, profile: Any,
                check_deadline: Callable[[], None]):
        aligned = self._pipeline.aligner.align(image, landmarks)
        check_deadline()
        validate_image_input(aligned, 256 * 256)
        if aligned.shape != (256, 256, 3):
            raise ValueError("aligned face must obey the 256px protocol")
        pose = self._pipeline.pose_estimator.estimate(aligned, FaceBox(x1=0.0, y1=0.0, x2=256.0, y2=256.0))
        check_deadline()
        if not isinstance(pose, Pose):
            pose = Pose(yaw=float(pose[0]), pitch=float(pose[1]), roll=float(pose[2]))
        warnings = evaluate_quality_warnings(image, box=box, landmarks=landmarks, pose=pose, profile=profile)
        check_deadline()
        try:
            feature = _normalized_feature(self._pipeline.embedder, aligned)
        except _CandidateFeatureInvalid:
            check_deadline()
            return None, warnings, ReasonCode.CANDIDATE_FEATURE_INVALID
        check_deadline()
        return feature, warnings, None
