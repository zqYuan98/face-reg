"""Candidate-level usability analysis for the independent presence capability."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from face_algorithm.core.contracts import (
    FeatureVersionMismatchError, ImageTooLargeError,
    InvalidImageError, extract_normalized_embedding, validate_image_input,
)
from face_algorithm.core.face_selection import FaceCandidateSelector, FaceSelectionResult
from face_algorithm.core.pipeline import FacePipeline
from face_algorithm.core.quality import evaluate_quality
from face_algorithm.presence.config import VideoPresencePolicy
from face_algorithm.schemas import FaceBox, Pose, ReasonCode
from face_algorithm.video.decoder import VideoDeadlineExceeded

__all__ = ["PresenceAnalysis", "PresenceAnalyzer"]


@dataclass(frozen=True, slots=True, repr=False)
class PresenceAnalysis:
    """Private analysis observations, deliberately excluding diagnostic array reprs."""

    features: tuple[np.ndarray, ...] = field(repr=False, compare=False, default=())
    candidate_count: int = 0
    candidate_limit_hit: bool = False
    reason_codes: tuple[ReasonCode, ...] = ()
    system_failure: bool = False


def _noop() -> None:
    pass


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PresenceAnalyzer:
    """Reuse an owned immutable face graph without changing strict capabilities."""

    _pipeline: FacePipeline = field(repr=False)
    _policy: VideoPresencePolicy
    _reference_profile: Any = field(repr=False)
    _frame_profile: Any = field(repr=False)
    _selector: FaceCandidateSelector = field(repr=False)

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
        object.__setattr__(self, "_selector", FaceCandidateSelector())

    def analyze_reference(self, image: object, *, check_deadline: Callable[[], None] = _noop) -> PresenceAnalysis:
        return self._analyze(image, reference=True, check_deadline=check_deadline)

    def analyze_frame(self, image: object, *, check_deadline: Callable[[], None] = _noop) -> PresenceAnalysis:
        return self._analyze(image, reference=False, check_deadline=check_deadline)

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
        candidate_count = 0
        limit_hit = False
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
            # Production detector exposes a bounded Sequence, never an unbounded iterable.
            if not isinstance(detected, Sequence):
                raise TypeError("detector output must be a bounded sequence")
            saturated = getattr(detected, "saturated", False)
            raw_count = getattr(detected, "raw_candidate_count", len(detected))
            if type(saturated) is not bool or type(raw_count) is not int or raw_count < len(detected):
                raise ValueError("invalid detector accounting")
            limit_hit = saturated or len(detected) > self._policy.max_face_candidates
            profile = self._reference_profile if reference else self._frame_profile
            if reference:
                selected = self._selector.select(corrected, detected, pipeline.landmarker, profile.face_selection, profile.landmark_geometry)
                check_deadline()
                feature, reasons, failed = self._assess(corrected, selected, profile, check_deadline)
                return PresenceAnalysis(features=() if feature is None else (feature,),
                    candidate_count=min(len(detected), self._policy.max_face_candidates),
                    candidate_limit_hit=limit_hit, reason_codes=reasons, system_failure=failed)
            reasons = []
            failed = False
            for box in detected[:self._policy.max_face_candidates]:
                check_deadline()
                candidate_count += 1
                selected = self._selector.select(corrected, (box,), pipeline.landmarker, profile.face_selection, profile.landmark_geometry)
                check_deadline()
                feature, candidate_reasons, candidate_failed = self._assess(corrected, selected, profile, check_deadline)
                reasons.extend(candidate_reasons)
                failed = failed or candidate_failed
                if feature is not None:
                    features.append(feature)
                if failed:
                    break
            if not detected:
                reasons.append(ReasonCode.NO_FACE)
            return PresenceAnalysis(features=tuple(features), candidate_count=candidate_count,
                candidate_limit_hit=limit_hit, reason_codes=tuple(dict.fromkeys(reasons)), system_failure=failed)
        except VideoDeadlineExceeded:
            raise
        except Exception:
            return PresenceAnalysis(features=tuple(features), candidate_count=candidate_count,
                candidate_limit_hit=limit_hit, reason_codes=(ReasonCode.INFERENCE_ERROR,), system_failure=True)

    def _assess(self, image: np.ndarray, selected: FaceSelectionResult, profile: Any,
                check_deadline: Callable[[], None]) -> tuple[np.ndarray | None, tuple[ReasonCode, ...], bool]:
        if selected.reason_code is not None:
            return None, (selected.reason_code,), selected.reason_code is ReasonCode.INFERENCE_ERROR
        box, landmarks = selected.box, selected.landmarks
        if box is None or landmarks is None:
            return None, (ReasonCode.INFERENCE_ERROR,), True
        # A clipped detector box is allowed, but its visible area must meet the floor.
        if box.width < profile.resolution.width or box.height < profile.resolution.height:
            return None, (ReasonCode.FACE_TOO_SMALL,), False
        height, width = image.shape[:2]
        margin = max(1.0, min(height, width) * profile.border_margin_ratio)
        if (np.any(landmarks[:, 0] <= margin) or np.any(landmarks[:, 1] <= margin)
                or np.any(landmarks[:, 0] >= width - margin) or np.any(landmarks[:, 1] >= height - margin)):
            return None, (ReasonCode.FACE_CROPPED,), False
        aligned = self._pipeline.aligner.align(image, landmarks)
        check_deadline()
        validate_image_input(aligned, 256 * 256)
        if aligned.shape != (256, 256, 3):
            return None, (ReasonCode.INFERENCE_ERROR,), True
        pose = self._pipeline.pose_estimator.estimate(aligned, FaceBox(x1=0.0, y1=0.0, x2=256.0, y2=256.0))
        check_deadline()
        if not isinstance(pose, Pose):
            pose = Pose(yaw=float(pose[0]), pitch=float(pose[1]), roll=float(pose[2]))
        x1, y1, x2, y2 = int(np.floor(box.x1)), int(np.floor(box.y1)), int(np.ceil(box.x2)), int(np.ceil(box.y2))
        visible = image[y1:y2, x1:x2]
        if visible.size == 0:
            return None, (ReasonCode.FACE_CROPPED,), False
        quality = evaluate_quality(visible, original_face_size=(box.width, box.height), pose=pose, profile=profile)
        check_deadline()
        if not quality.passed:
            return None, quality.reason_codes, False
        try:
            feature = extract_normalized_embedding(self._pipeline.embedder, aligned)
        except FeatureVersionMismatchError:
            return None, (ReasonCode.FEATURE_VERSION_MISMATCH,), True
        except Exception:
            return None, (ReasonCode.FEATURE_FAILED,), True
        check_deadline()
        feature.setflags(write=False)
        return feature, (), False
