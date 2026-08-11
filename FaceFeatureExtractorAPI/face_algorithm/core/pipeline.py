"""Dependency-injected one-image face analysis pipeline."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import numpy as np

from face_algorithm.core.contracts import (
    FeatureVersionMismatchError,
    ImageTooLargeError,
    InvalidImageError,
    extract_normalized_embedding,
    validate_image_input,
)
from face_algorithm.core.protocols import (
    FaceAligner,
    FaceDetector,
    FaceEmbedder,
    ImageRotator,
    LandmarkDetector,
    PoseEstimator,
)
from face_algorithm.core.quality import evaluate_quality
from face_algorithm.runtime.model_registry import VerifiedModelEvidence
from face_algorithm.schemas import (
    FaceAnalysisResult,
    FaceBox,
    FaceLandmarks,
    FeatureVersion,
    LandmarkPoint,
    Pose,
    QualityResult,
    ReasonCode,
    Suggestion,
)

__all__ = ["FacePipeline", "FaceQualityAnalysis", "TrustedFaceAnalysis"]


@dataclass(frozen=True, slots=True, repr=False)
class FaceQualityAnalysis:
    """Internal image-quality result with no biometric feature field."""

    public: FaceAnalysisResult
    corrected_image: np.ndarray | None
    aligned_face: np.ndarray | None


@dataclass(frozen=True, slots=True, repr=False)
class TrustedFaceAnalysis:
    """Internal result; unlike the public schema this may carry biometric data."""

    public: FaceAnalysisResult
    embedding: np.ndarray | None
    corrected_image: np.ndarray | None
    aligned_face: np.ndarray | None


class FacePipeline:
    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("FacePipeline bindings are immutable after initialization")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        *,
        rotator: ImageRotator,
        detector: FaceDetector,
        landmarker: LandmarkDetector,
        aligner: FaceAligner,
        pose_estimator: PoseEstimator,
        embedder: FaceEmbedder,
        model_versions: dict[str, str],
        config_version: str,
        max_image_pixels: int,
        model_evidence: Mapping[str, VerifiedModelEvidence] | None = None,
    ) -> None:
        object.__setattr__(self, "_sealed", False)
        if isinstance(max_image_pixels, bool) or not isinstance(max_image_pixels, int):
            raise TypeError("max_image_pixels must be a positive integer")
        if max_image_pixels <= 0:
            raise ValueError("max_image_pixels must be a positive integer")
        self.rotator = rotator
        self.detector = detector
        self.landmarker = landmarker
        self.aligner = aligner
        self.pose_estimator = pose_estimator
        self.embedder = embedder
        self.model_versions = MappingProxyType(dict(model_versions))
        self.config_version = config_version
        self.max_image_pixels = max_image_pixels
        self.model_evidence = MappingProxyType(dict(model_evidence or {}))
        object.__setattr__(self, "_sealed", True)

    def analyze_public(self, image: np.ndarray, profile: Any) -> FaceAnalysisResult:
        """Analyze an image without ever returning an embedding."""

        return self.analyze_trusted(image, profile).public

    def analyze_trusted(self, image: np.ndarray, profile: Any) -> TrustedFaceAnalysis:
        """Analyze an image and return a normalized embedding only on PASS."""

        return self._analyze_trusted(image, profile, extract_embedding=True)

    def analyze_quality(self, image: np.ndarray, profile: Any) -> FaceQualityAnalysis:
        """Run face usability checks without invoking the biometric embedder."""

        trusted = self._analyze_trusted(image, profile, extract_embedding=False)
        return FaceQualityAnalysis(
            public=trusted.public,
            corrected_image=trusted.corrected_image,
            aligned_face=trusted.aligned_face,
        )

    def _analyze_trusted(
        self,
        image: np.ndarray,
        profile: Any,
        *,
        extract_embedding: bool,
    ) -> TrustedFaceAnalysis:

        started = time.perf_counter()
        trace_id = uuid.uuid4().hex
        steps: list[str] = []
        original_width: int | None = None
        original_height: int | None = None

        def finish(
            reasons: tuple[ReasonCode, ...],
            *,
            box: FaceBox | None = None,
            landmarks: FaceLandmarks | None = None,
            quality: QualityResult | None = None,
            pose: Pose | None = None,
            quarter_turns: int = 0,
            corrected: np.ndarray | None = None,
            aligned: np.ndarray | None = None,
            embedding: np.ndarray | None = None,
            suggestion: Suggestion | None = None,
            completed: bool = True,
        ) -> TrustedFaceAnalysis:
            if suggestion is None:
                suggestion = Suggestion.PASS if not reasons else Suggestion.RECAPTURE
            public = FaceAnalysisResult(
                success=completed,
                suggestion=suggestion,
                reason_codes=reasons,
                model_versions=self.model_versions,
                feature_version=FeatureVersion.RECOGNIZER_V1_1024,
                config_version=self.config_version,
                processing_time_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
                trace_id=trace_id,
                original_width=original_width,
                original_height=original_height,
                box=box,
                landmarks=landmarks,
                quality=quality,
                pose=pose,
                rotation_quarter_turns=quarter_turns,
                processing_steps=tuple(steps),
            )
            return TrustedFaceAnalysis(
                public=public,
                embedding=embedding,
                corrected_image=corrected,
                aligned_face=aligned,
            )

        try:
            original_height, original_width = validate_image_input(
                image,
                self.max_image_pixels,
            )
        except InvalidImageError:
            return finish((ReasonCode.INVALID_IMAGE,), completed=False)
        except ImageTooLargeError as error:
            original_height = error.height
            original_width = error.width
            return finish((ReasonCode.IMAGE_TOO_LARGE,), completed=False)
        original = image.copy()

        try:
            rotation = self.rotator.correct(original)
            corrected = rotation.corrected_image
            quarter_turns = int(rotation.quarter_turns)
            corrected_height, corrected_width = validate_image_input(
                corrected,
                self.max_image_pixels,
            )
            if quarter_turns not in range(4):
                raise ValueError("invalid rotation result")
            expected_shape = (
                (original_height, original_width)
                if quarter_turns in (0, 2)
                else (original_width, original_height)
            )
            if (corrected_height, corrected_width) != expected_shape:
                raise ValueError("rotation result dimensions do not match quarter_turns")
            steps.append("rotation")
        except Exception:
            return finish(
                (ReasonCode.INFERENCE_ERROR,),
                suggestion=Suggestion.REVIEW,
                completed=False,
            )

        try:
            boxes = list(self.detector.detect(corrected))
            steps.append("detection")
        except Exception:
            return finish(
                (ReasonCode.INFERENCE_ERROR,),
                quarter_turns=quarter_turns,
                corrected=corrected,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )
        if not boxes:
            return finish(
                (ReasonCode.NO_FACE,),
                quarter_turns=quarter_turns,
                corrected=corrected,
            )
        if profile.require_single_face and len(boxes) != 1:
            return finish(
                (ReasonCode.MULTIPLE_FACES,),
                quarter_turns=quarter_turns,
                corrected=corrected,
            )

        raw_box = boxes[0]
        if not isinstance(raw_box, FaceBox):
            try:
                raw_box = FaceBox(
                    x1=float(raw_box.x1),
                    y1=float(raw_box.y1),
                    x2=float(raw_box.x2),
                    y2=float(raw_box.y2),
                    confidence=float(getattr(raw_box, "score", 1.0)),
                )
            except Exception:
                return finish(
                    (ReasonCode.INFERENCE_ERROR,),
                    quarter_turns=quarter_turns,
                    corrected=corrected,
                    suggestion=Suggestion.REVIEW,
                    completed=False,
                )
        if (
            raw_box.width < profile.resolution.width
            or raw_box.height < profile.resolution.height
        ):
            return finish(
                (ReasonCode.FACE_TOO_SMALL,),
                box=_clip_box(raw_box, corrected.shape),
                quarter_turns=quarter_turns,
                corrected=corrected,
            )

        clipped_box = _clip_box(raw_box, corrected.shape)
        if clipped_box is None or _box_touches_margin(
            raw_box,
            corrected.shape,
            profile.border_margin_ratio,
        ):
            return finish(
                (ReasonCode.FACE_CROPPED,),
                box=clipped_box,
                quarter_turns=quarter_turns,
                corrected=corrected,
            )

        try:
            landmark_array = np.asarray(
                self.landmarker.detect(corrected, clipped_box),
                dtype=np.float32,
            )
            steps.append("landmarks")
        except Exception:
            return finish(
                (ReasonCode.INFERENCE_ERROR,),
                box=clipped_box,
                quarter_turns=quarter_turns,
                corrected=corrected,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )
        if landmark_array.shape != (5, 2) or not np.all(np.isfinite(landmark_array)):
            return finish(
                (ReasonCode.INFERENCE_ERROR,),
                box=clipped_box,
                quarter_turns=quarter_turns,
                corrected=corrected,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )
        public_landmarks = FaceLandmarks(
            points=tuple(
                LandmarkPoint(x=float(point[0]), y=float(point[1]))
                for point in landmark_array
            )
        )
        if _landmarks_touch_margin(
            landmark_array,
            corrected.shape,
            profile.border_margin_ratio,
        ):
            return finish(
                (ReasonCode.FACE_CROPPED,),
                box=clipped_box,
                landmarks=public_landmarks,
                quarter_turns=quarter_turns,
                corrected=corrected,
            )
        if not _valid_landmarks(landmark_array, clipped_box, profile.landmark_geometry):
            return finish(
                (ReasonCode.LANDMARK_FAILED,),
                box=clipped_box,
                quarter_turns=quarter_turns,
                corrected=corrected,
            )

        try:
            aligned = self.aligner.align(corrected, landmark_array)
            steps.append("alignment")
        except Exception:
            return finish(
                (ReasonCode.INFERENCE_ERROR,),
                box=clipped_box,
                landmarks=public_landmarks,
                quarter_turns=quarter_turns,
                corrected=corrected,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )
        if not _is_valid_aligned_face(aligned):
            return finish(
                (ReasonCode.INFERENCE_ERROR,),
                box=clipped_box,
                landmarks=public_landmarks,
                quarter_turns=quarter_turns,
                corrected=corrected,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )

        try:
            aligned_height, aligned_width = aligned.shape[:2]
            aligned_box = FaceBox(
                x1=0.0,
                y1=0.0,
                x2=float(aligned_width),
                y2=float(aligned_height),
            )
            # The legacy FSA-Net protocol estimates pose from the 256x256
            # aligned face, not from the detector crop in the source image.
            pose = self.pose_estimator.estimate(aligned, aligned_box)
            if not isinstance(pose, Pose):
                pose = Pose(
                    yaw=float(pose[0]),
                    pitch=float(pose[1]),
                    roll=float(pose[2]),
                )
            steps.append("quality")
            x1, y1, x2, y2 = (
                int(np.floor(clipped_box.x1)),
                int(np.floor(clipped_box.y1)),
                int(np.ceil(clipped_box.x2)),
                int(np.ceil(clipped_box.y2)),
            )
            face_region = corrected[y1:y2, x1:x2]
            quality = evaluate_quality(
                face_region,
                original_face_size=(raw_box.width, raw_box.height),
                pose=pose,
                profile=profile,
            )
        except Exception:
            return finish(
                (ReasonCode.INFERENCE_ERROR,),
                box=clipped_box,
                landmarks=public_landmarks,
                quarter_turns=quarter_turns,
                corrected=corrected,
                aligned=aligned,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )
        if not quality.passed:
            return finish(
                quality.reason_codes,
                box=clipped_box,
                landmarks=public_landmarks,
                quality=quality,
                pose=pose,
                quarter_turns=quarter_turns,
                corrected=corrected,
                aligned=aligned,
            )

        if not extract_embedding:
            return finish(
                (),
                box=clipped_box,
                landmarks=public_landmarks,
                quality=quality,
                pose=pose,
                quarter_turns=quarter_turns,
                corrected=corrected,
                aligned=aligned,
            )

        try:
            embedding = extract_normalized_embedding(self.embedder, aligned)
            steps.append("feature_extraction")
        except FeatureVersionMismatchError:
            return finish(
                (ReasonCode.FEATURE_VERSION_MISMATCH,),
                box=clipped_box,
                landmarks=public_landmarks,
                quality=quality,
                pose=pose,
                quarter_turns=quarter_turns,
                corrected=corrected,
                aligned=aligned,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )
        except Exception:
            return finish(
                (ReasonCode.FEATURE_FAILED,),
                box=clipped_box,
                landmarks=public_landmarks,
                quality=quality,
                pose=pose,
                quarter_turns=quarter_turns,
                corrected=corrected,
                aligned=aligned,
                suggestion=Suggestion.REVIEW,
                completed=False,
            )
        return finish(
            (),
            box=clipped_box,
            landmarks=public_landmarks,
            quality=quality,
            pose=pose,
            quarter_turns=quarter_turns,
            corrected=corrected,
            aligned=aligned,
            embedding=embedding,
        )


def _is_valid_image(image: object) -> bool:
    try:
        validate_image_input(image, np.iinfo(np.int64).max)
    except ValueError:
        return False
    return True


def _is_valid_aligned_face(image: object) -> bool:
    return bool(_is_valid_image(image) and image.shape == (256, 256, 3))


def _clip_box(box: FaceBox, shape: tuple[int, ...]) -> FaceBox | None:
    height, width = shape[:2]
    x1 = float(np.clip(box.x1, 0.0, float(width)))
    y1 = float(np.clip(box.y1, 0.0, float(height)))
    x2 = float(np.clip(box.x2, 0.0, float(width)))
    y2 = float(np.clip(box.y2, 0.0, float(height)))
    if x2 <= x1 or y2 <= y1:
        return None
    return FaceBox(
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        confidence=box.confidence,
    )


def _box_touches_margin(
    box: FaceBox,
    shape: tuple[int, ...],
    margin_ratio: float,
) -> bool:
    height, width = shape[:2]
    margin = max(1.0, min(height, width) * float(margin_ratio))
    return bool(
        box.x1 <= margin
        or box.y1 <= margin
        or box.x2 >= width - margin
        or box.y2 >= height - margin
    )


def _landmarks_touch_margin(
    landmarks: np.ndarray,
    shape: tuple[int, ...],
    margin_ratio: float,
) -> bool:
    height, width = shape[:2]
    margin = max(1.0, min(height, width) * float(margin_ratio))
    return bool(
        np.any(landmarks[:, 0] <= margin)
        or np.any(landmarks[:, 1] <= margin)
        or np.any(landmarks[:, 0] >= width - margin)
        or np.any(landmarks[:, 1] >= height - margin)
    )


def _valid_landmarks(landmarks: np.ndarray, box: FaceBox, geometry: Any) -> bool:
    if landmarks.shape != (5, 2) or not np.all(np.isfinite(landmarks)):
        return False
    tolerance_x = box.width * geometry.bbox_tolerance_ratio
    tolerance_y = box.height * geometry.bbox_tolerance_ratio
    if geometry.require_points_inside_bbox and (
        np.any(landmarks[:, 0] < box.x1 - tolerance_x)
        or np.any(landmarks[:, 0] > box.x2 + tolerance_x)
        or np.any(landmarks[:, 1] < box.y1 - tolerance_y)
        or np.any(landmarks[:, 1] > box.y2 + tolerance_y)
    ):
        return False
    left_eye, right_eye, nose, left_mouth, right_mouth = landmarks
    if not (left_eye[0] < right_eye[0] and left_mouth[0] < right_mouth[0]):
        return False
    if not (max(left_eye[1], right_eye[1]) < nose[1] < min(left_mouth[1], right_mouth[1])):
        return False
    if (right_eye[0] - left_eye[0]) / box.width < geometry.min_eye_distance_ratio:
        return False
    if (right_mouth[0] - left_mouth[0]) / box.width < geometry.min_mouth_width_ratio:
        return False
    if abs(right_eye[1] - left_eye[1]) / box.height > geometry.max_eye_vertical_difference_ratio:
        return False
    return True
