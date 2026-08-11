"""Backward-compatible facade over the modular face pipeline.

New application code should use :class:`face_algorithm.core.pipeline.FacePipeline`.
This module preserves the exact legacy result dataclasses and helper methods
used by the existing FastAPI service.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from face_algorithm.core.adapters import get_default_runtime
from face_algorithm.core.contracts import (
    extract_normalized_embedding,
    validate_image_input,
)
from face_algorithm.core.pipeline import FacePipeline
from face_algorithm.core.quality import evaluate_quality
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import FaceBox, Pose
from models.facedetector import Box


@dataclass
class FaceInfo:
    bbox: Tuple[float, float, float, float]
    landmarks: List[Tuple[float, float]]
    confidence: float
    quality_scores: Dict[str, Any]
    feature: Optional[np.ndarray] = None


@dataclass
class FeatureExtractionResult:
    success: bool
    faces: List[FaceInfo]
    processing_time: float
    error_message: Optional[str] = None


def _legacy_quality(public_result) -> Dict[str, Any]:
    quality = public_result.quality
    if quality is None:
        return {
            "brightness": {"passed": False, "description": "brightness"},
            "resolution": {"passed": False, "description": "resolution"},
            "clarity": {"passed": False, "description": "clarity"},
            "pose": {"passed": False, "description": "pose"},
            "overall": {"passed": False, "description": "overall"},
        }
    by_name = {metric.name: metric for metric in quality.metrics}
    pose_passed = all(
        by_name.get(name) is not None and by_name[name].passed
        for name in ("yaw", "pitch", "roll")
    )
    pose = public_result.pose
    return {
        "brightness": {
            "passed": by_name["brightness"].passed,
            "value": by_name["brightness"].value,
            "description": "brightness",
        },
        "resolution": {
            "passed": by_name["resolution"].passed,
            "value": by_name["resolution"].value,
            "description": "resolution",
        },
        "clarity": {
            "passed": by_name["clarity"].passed,
            "value": by_name["clarity"].value,
            "description": "clarity",
        },
        "pose": {
            "passed": pose_passed,
            "description": "pose",
            "yaw": pose.yaw if pose else None,
            "pitch": pose.pitch if pose else None,
            "roll": pose.roll if pose else None,
        },
        "overall": {
            "passed": quality.passed,
            "description": "overall",
        },
    }


class FaceFeatureExtractor:
    """Legacy API whose implementation is delegated to one modular pipeline."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        pipeline: FacePipeline | None = None,
        profile: Any | None = None,
    ) -> None:
        # ``config`` stays accepted for call compatibility. Runtime policy now
        # comes from the strict algorithm YAML instead of duplicated literals.
        self.config = config or {}
        if pipeline is None:
            runtime_pipeline, runtime_config = get_default_runtime()
            self.pipeline = runtime_pipeline
            self.profile = (
                profile
                if profile is not None
                else runtime_config.quality_profiles.entry_verify
            )
        else:
            self.pipeline = pipeline
            self.profile = (
                profile
                if profile is not None
                else load_config().quality_profiles.entry_verify
            )
        self.stats = {
            "total_extractions": 0,
            "successful_extractions": 0,
            "quality_filtered": 0,
            "average_processing_time": 0.0,
        }

    def correct_image_rotation(self, image: np.ndarray) -> np.ndarray:
        validate_image_input(image, self.pipeline.max_image_pixels)
        return self.pipeline.rotator.correct(image).corrected_image

    def detect_faces(self, image: np.ndarray) -> List[Box]:
        """Detect in the original image coordinate space (legacy behavior)."""

        validate_image_input(image, self.pipeline.max_image_pixels)
        detected = self.pipeline.detector.detect(image)
        boxes: list[Box] = []
        for face in detected:
            if isinstance(face, FaceBox):
                boxes.append(
                    Box(
                        face.x1,
                        face.y1,
                        face.x2,
                        face.y2,
                        face.confidence,
                    )
                )
            else:
                boxes.append(face)
        return boxes

    def extract_landmarks(
        self,
        image: np.ndarray,
        boxes: List[Box],
    ) -> List[List[Tuple[float, float]]]:
        validate_image_input(image, self.pipeline.max_image_pixels)
        landmarks: list[list[tuple[float, float]]] = []
        for box in boxes:
            face = FaceBox(
                x1=float(box.x1),
                y1=float(box.y1),
                x2=float(box.x2),
                y2=float(box.y2),
                confidence=float(box.score),
            )
            points = np.asarray(self.pipeline.landmarker.detect(image, face))
            landmarks.append([(float(x), float(y)) for x, y in points])
        return landmarks

    def align_face(
        self,
        image: np.ndarray,
        landmarks: List[Tuple[float, float]],
    ) -> Optional[np.ndarray]:
        try:
            validate_image_input(image, self.pipeline.max_image_pixels)
            aligned = self.pipeline.aligner.align(
                image,
                np.asarray(landmarks, dtype=np.float32),
            )
        except Exception:
            return None
        return aligned if isinstance(aligned, np.ndarray) and aligned.size else None

    def extract_feature(self, aligned_face: np.ndarray) -> Optional[np.ndarray]:
        try:
            validate_image_input(aligned_face, self.pipeline.max_image_pixels)
            feature = extract_normalized_embedding(
                self.pipeline.embedder,
                aligned_face,
            )
        except Exception:
            return None
        return feature

    def assess_quality(
        self,
        image: np.ndarray,
        box: Box,
        aligned_face: np.ndarray,
    ) -> Dict[str, Any]:
        validate_image_input(image, self.pipeline.max_image_pixels)
        validate_image_input(aligned_face, self.pipeline.max_image_pixels)
        face = FaceBox(
            x1=float(box.x1),
            y1=float(box.y1),
            x2=float(box.x2),
            y2=float(box.y2),
            confidence=float(box.score),
        )
        height, width = image.shape[:2]
        x1 = max(0, int(np.floor(face.x1)))
        y1 = max(0, int(np.floor(face.y1)))
        x2 = min(width, int(np.ceil(face.x2)))
        y2 = min(height, int(np.ceil(face.y2)))
        aligned_height, aligned_width = aligned_face.shape[:2]
        aligned_box = FaceBox(
            x1=0.0,
            y1=0.0,
            x2=float(aligned_width),
            y2=float(aligned_height),
        )
        pose = self.pipeline.pose_estimator.estimate(aligned_face, aligned_box)
        if not isinstance(pose, Pose):
            pose = Pose(yaw=float(pose[0]), pitch=float(pose[1]), roll=float(pose[2]))
        quality = evaluate_quality(
            image[y1:y2, x1:x2],
            original_face_size=(face.width, face.height),
            pose=pose,
            profile=self.profile,
        )
        result = type("LegacyQualityView", (), {"quality": quality, "pose": pose})()
        return _legacy_quality(result)

    def extract_features(
        self,
        image: np.ndarray,
        return_all_faces: bool = False,
        quality_filter: bool = True,
    ) -> FeatureExtractionResult:
        del return_all_faces, quality_filter
        self.stats["total_extractions"] += 1
        trusted = self.pipeline.analyze_trusted(image, self.profile)
        public = trusted.public
        processing_time = public.processing_time_ms / 1000.0
        previous_count = self.stats["total_extractions"] - 1
        self.stats["average_processing_time"] = (
            self.stats["average_processing_time"] * previous_count + processing_time
        ) / self.stats["total_extractions"]

        if not public.success or trusted.embedding is None:
            if public.quality is not None and not public.quality.passed:
                self.stats["quality_filtered"] += 1
            error = ",".join(reason.value for reason in public.reason_codes) or None
            return FeatureExtractionResult(
                success=False,
                faces=[],
                processing_time=processing_time,
                error_message=error,
            )

        assert public.box is not None and public.landmarks is not None
        self.stats["successful_extractions"] += 1
        face = FaceInfo(
            bbox=(
                public.box.x1,
                public.box.y1,
                public.box.x2,
                public.box.y2,
            ),
            landmarks=[
                (point.x, point.y)
                for point in public.landmarks.points
            ],
            confidence=public.box.confidence,
            quality_scores=_legacy_quality(public),
            feature=trusted.embedding.copy(),
        )
        return FeatureExtractionResult(
            success=True,
            faces=[face],
            processing_time=processing_time,
        )

    def extract_single_feature(self, image: np.ndarray) -> Optional[np.ndarray]:
        result = self.extract_features(
            image,
            return_all_faces=False,
            quality_filter=True,
        )
        return result.faces[0].feature if result.success and result.faces else None

    def extract_multiple_features(self, image: np.ndarray) -> List[np.ndarray]:
        result = self.extract_features(
            image,
            return_all_faces=True,
            quality_filter=True,
        )
        return [
            face.feature
            for face in result.faces
            if face.feature is not None
        ]

    def get_statistics(self) -> Dict[str, Any]:
        total = max(self.stats["total_extractions"], 1)
        successful = self.stats["successful_extractions"]
        return {
            **self.stats,
            "success_rate": successful / total,
            "quality_pass_rate": (
                successful - self.stats["quality_filtered"]
            ) / max(successful, 1),
        }


_default_extractor: FaceFeatureExtractor | None = None
_default_extractor_lock = threading.Lock()


def _get_default_extractor() -> FaceFeatureExtractor:
    global _default_extractor
    if _default_extractor is None:
        with _default_extractor_lock:
            if _default_extractor is None:
                _default_extractor = FaceFeatureExtractor()
    return _default_extractor


def extract_face_feature(
    image: np.ndarray,
    config: Optional[Dict] = None,
) -> Optional[np.ndarray]:
    extractor = FaceFeatureExtractor(config) if config is not None else _get_default_extractor()
    return extractor.extract_single_feature(image)


def extract_face_features(
    image: np.ndarray,
    config: Optional[Dict] = None,
) -> List[np.ndarray]:
    extractor = FaceFeatureExtractor(config) if config is not None else _get_default_extractor()
    return extractor.extract_multiple_features(image)
