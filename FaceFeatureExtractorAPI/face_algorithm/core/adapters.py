"""Thin adapters around the seven existing ONNX models."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort

from face_algorithm.core.face_selection import FaceCandidateBatch
from face_algorithm.core.pipeline import FacePipeline
from face_algorithm.core.protocols import RotationResult
from face_algorithm.runtime.config import AlgorithmConfig, load_config
from face_algorithm.runtime.model_registry import (
    ModelContract,
    ModelRegistry,
    VerifiedArtifact,
    VerifiedModelEvidence,
)
from face_algorithm.runtime.model_factories import create_portrait_matting_factory
from face_algorithm.schemas import FEATURE_DIMENSION, FaceBox, Pose
from models.facealign import FaceAlign
from models.facedetector import FaceBoxesV2
from models.facelandmarks5er import Landmark5er
from models.facerecoger import FaceRecoger
from models.imgchecker import QualityOfPose

__all__ = [
    "FaceBoxesAdapter",
    "FaceRecognizerAdapter",
    "Landmark5Adapter",
    "PoseAdapter",
    "RotationAdapter",
    "build_face_pipeline",
    "build_face_quality_pipeline",
    "build_owned_face_pipeline",
    "build_owned_face_quality_pipeline",
    "create_model_registry",
    "get_default_pipeline",
    "get_default_runtime",
    "validate_face_recognizer_contract",
]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FACE_PIPELINE_MODEL_NAMES = (
    "rotation_classifier",
    "face_detector",
    "face_landmark_primary",
    "face_landmark_secondary",
    "pose_var",
    "pose_conv",
    "face_recognizer",
)
_FACE_QUALITY_MODEL_NAMES = tuple(
    name for name in _FACE_PIPELINE_MODEL_NAMES if name != "face_recognizer"
)
_RAW_FACE_CANDIDATE_SCAN_LIMIT = 64


def _onnx_factory(intra_op_num_threads: int):
    def create(artifact: VerifiedArtifact) -> ort.InferenceSession:
        options = ort.SessionOptions()
        options.intra_op_num_threads = intra_op_num_threads
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # The verified byte buffer is the sole model source. Reopening
        # artifact.entry.path here would reintroduce a check/use race.
        return ort.InferenceSession(
            artifact.data,
            options,
            providers=artifact.entry.providers,
        )

    return create


def validate_face_recognizer_contract(
    session: object,
    _artifact: object,
) -> ModelContract:
    """Prove the real recognizer contract with a raw dummy inference."""

    if not isinstance(session, ort.InferenceSession):
        raise TypeError("recognizer factory did not return an ONNX Runtime session")
    inputs = {node.name: node for node in session.get_inputs()}
    outputs = {node.name: node for node in session.get_outputs()}
    if "_input_123" not in inputs or "fc1_act_50" not in outputs:
        raise ValueError("recognizer input/output names do not match v1")
    result = session.run(
        ["fc1_act_50"],
        {"_input_123": np.zeros((1, 3, 248, 248), dtype=np.float32)},
    )
    if len(result) != 1:
        raise ValueError("recognizer dummy inference returned an unexpected output count")
    raw = result[0]
    if not isinstance(raw, np.ndarray) or raw.dtype != np.float32:
        raise ValueError("recognizer output must be a float32 ndarray")
    if raw.shape != (1, FEATURE_DIMENSION, 1, 1):
        raise ValueError(f"recognizer output shape is {raw.shape}, expected (1, 1024, 1, 1)")
    return ModelContract(
        feature_version="recognizer_v1_1024",
        output_dimension=FEATURE_DIMENSION,
    )


def create_model_registry(
    manifest_path: str | Path,
    config: AlgorithmConfig,
) -> ModelRegistry:
    """Create lazy factories for seven core models and optional portrait matting."""

    factory = _onnx_factory(config.models.intra_op_num_threads)
    required_names = (
        "face_detector",
        "face_landmark_primary",
        "face_landmark_secondary",
        "face_recognizer",
        "rotation_classifier",
        "pose_var",
        "pose_conv",
    )
    factories = {name: factory for name in required_names}
    if config.models.portrait_matting.enabled:
        factories["portrait_matting"] = create_portrait_matting_factory(
            max_image_pixels=config.resources.max_image_pixels,
        )
    return ModelRegistry(
        manifest_path,
        factories,
        contract_validators={
            "face_recognizer": validate_face_recognizer_contract,
        },
    )


class RotationAdapter:
    def __init__(self, session: ort.InferenceSession) -> None:
        self.session = session
        self.input_name = session.get_inputs()[0].name
        self.output_name = session.get_outputs()[0].name

    def correct(self, image: np.ndarray) -> RotationResult:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (256, 256))
        gray = gray[16:240, 16:240]
        tensor = np.stack([gray] * 3, axis=-1).astype(np.float32) / np.float32(255.0)
        tensor = tensor.transpose(2, 0, 1)[None, ...]
        scores = self.session.run(
            [self.output_name],
            {self.input_name: tensor},
        )[0]
        if not isinstance(scores, np.ndarray):
            raise TypeError("rotation scores must be a NumPy array")
        if scores.dtype != np.float32:
            raise ValueError("rotation scores must have dtype float32")
        if scores.shape != (1, 4):
            raise ValueError("rotation scores must have shape (1, 4)")
        if not np.all(np.isfinite(scores)):
            raise ValueError("rotation scores must be finite")
        quarter_turns = int(np.argmax(scores[0]))
        if quarter_turns == 1:
            corrected = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        elif quarter_turns == 2:
            corrected = cv2.rotate(image, cv2.ROTATE_180)
        elif quarter_turns == 3:
            corrected = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        else:
            corrected = image.copy()
        return RotationResult(
            corrected_image=corrected,
            quarter_turns=quarter_turns,
        )


class FaceBoxesAdapter:
    def __init__(
        self,
        session: ort.InferenceSession,
        *,
        score_threshold: float = 0.35,
        iou_threshold: float = 0.45,
        max_faces_to_detect: int = 6,
    ) -> None:
        if isinstance(max_faces_to_detect, bool) or not isinstance(
            max_faces_to_detect,
            int,
        ):
            raise TypeError("max_faces_to_detect must be an integer")
        if max_faces_to_detect < 2:
            raise ValueError("max_faces_to_detect must be at least two")
        if max_faces_to_detect > 6:
            raise ValueError("max_faces_to_detect cannot exceed six")
        self.detector = FaceBoxesV2(session=session)
        self.score_threshold = score_threshold
        self.iou_threshold = iou_threshold
        self.max_faces_to_detect = max_faces_to_detect
        self.raw_candidate_scan_limit = _RAW_FACE_CANDIDATE_SCAN_LIMIT

    def detect(self, image: np.ndarray) -> FaceCandidateBatch:
        if not (
            isinstance(image, np.ndarray)
            and image.ndim >= 2
            and image.shape[0] > 0
            and image.shape[1] > 0
        ):
            raise ValueError("face detector image must have positive dimensions")
        image_height, image_width = image.shape[:2]
        boxes = self.detector.detect(
            image,
            score_threshold=self.score_threshold,
            iou_threshold=self.iou_threshold,
            topk=self.raw_candidate_scan_limit + 1,
        )
        raw_boxes = [] if boxes is None else list(boxes)
        observed_raw_candidate_count = len(raw_boxes)
        raw_scan_saturated = len(raw_boxes) > self.raw_candidate_scan_limit
        raw_boxes = raw_boxes[: self.raw_candidate_scan_limit]
        result: list[FaceBox] = []
        valid_intersection_count = 0
        for box in raw_boxes:
            try:
                x1 = float(box.x1)
                y1 = float(box.y1)
                x2 = float(box.x2)
                y2 = float(box.y2)
                score = float(box.score)
            except Exception as error:
                raise ValueError("face detector returned a malformed box") from error
            values = np.asarray((x1, y1, x2, y2, score), dtype=np.float64)
            if not np.all(np.isfinite(values)):
                raise ValueError("face detector box values must be finite")
            if x2 <= x1 or y2 <= y1:
                raise ValueError("face detector box must have positive extents")
            if not 0.0 <= score <= 1.0:
                raise ValueError("face detector score must be in [0, 1]")
            if (
                min(x2, float(image_width)) <= max(x1, 0.0)
                or min(y2, float(image_height)) <= max(y1, 0.0)
            ):
                continue
            valid_intersection_count += 1
            if len(result) < self.max_faces_to_detect:
                result.append(
                    FaceBox(
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        confidence=score,
                    )
                )
        return FaceCandidateBatch(
            candidates=tuple(result),
            raw_candidate_count=observed_raw_candidate_count,
            saturated=(
                raw_scan_saturated
                or valid_intersection_count > self.max_faces_to_detect
            ),
        )


class Landmark5Adapter:
    def __init__(
        self,
        primary_session: ort.InferenceSession,
        secondary_session: ort.InferenceSession,
    ) -> None:
        self.landmarker = Landmark5er(
            session1=primary_session,
            session2=secondary_session,
        )

    def detect(self, image: np.ndarray, face: FaceBox) -> np.ndarray:
        landmarks = self.landmarker.inference(
            image,
            (face.x1, face.y1, face.x2, face.y2),
        )
        return np.asarray(landmarks, dtype=np.float32).reshape(5, 2)


class FaceAlignAdapter:
    def __init__(self) -> None:
        self.aligner = FaceAlign()

    def align(self, image: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        return self.aligner.align(image, landmarks.tolist())


class FaceRecognizerAdapter:
    feature_version = "recognizer_v1_1024"
    feature_dim = FEATURE_DIMENSION

    def __init__(self, session: ort.InferenceSession) -> None:
        self.recognizer = FaceRecoger(session=session)

    def extract(self, aligned_face: np.ndarray) -> np.ndarray:
        return self.recognizer.inference(aligned_face)


class PoseAdapter:
    def __init__(
        self,
        var_session: ort.InferenceSession,
        conv_session: ort.InferenceSession,
    ) -> None:
        self.estimator = QualityOfPose(
            var_session=var_session,
            conv_session=conv_session,
        )

    def estimate(self, image: np.ndarray, face: FaceBox) -> Pose:
        height, width = image.shape[:2]
        x1 = max(0, int(np.floor(face.x1)))
        y1 = max(0, int(np.floor(face.y1)))
        x2 = min(width, int(np.ceil(face.x2)))
        y2 = min(height, int(np.ceil(face.y2)))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            raise ValueError("pose face crop is empty")
        yaw, pitch, roll = self.estimator.detect_angle(crop)
        return Pose(yaw=yaw, pitch=pitch, roll=roll)


def build_face_pipeline(
    *,
    config: AlgorithmConfig,
    registry: ModelRegistry,
) -> FacePipeline:
    """Assemble adapters from registry-cached process sessions."""

    handles = {
        name: registry.get_with_evidence(name)
        for name in _FACE_PIPELINE_MODEL_NAMES
    }
    sessions = {name: handle.instance for name, handle in handles.items()}
    evidence: dict[str, VerifiedModelEvidence] = {
        name: handle.evidence for name, handle in handles.items()
    }
    versions = {name: handle.evidence.version for name, handle in handles.items()}
    return FacePipeline(
        rotator=RotationAdapter(sessions["rotation_classifier"]),
        detector=FaceBoxesAdapter(sessions["face_detector"]),
        landmarker=Landmark5Adapter(
            sessions["face_landmark_primary"],
            sessions["face_landmark_secondary"],
        ),
        aligner=FaceAlignAdapter(),
        pose_estimator=PoseAdapter(
            sessions["pose_var"],
            sessions["pose_conv"],
        ),
        embedder=FaceRecognizerAdapter(sessions["face_recognizer"]),
        model_versions=versions,
        config_version=config.config_version,
        max_image_pixels=config.resources.max_image_pixels,
        model_evidence=evidence,
    )


class _ForbiddenPortraitEmbedder:
    """Fail loudly if a quality-only pipeline is misused for recognition."""

    feature_version = "recognizer_v1_1024"
    feature_dim = FEATURE_DIMENSION

    def extract(self, _aligned_face: np.ndarray) -> np.ndarray:
        raise RuntimeError("quality-only pipeline cannot extract face embeddings")


def build_face_quality_pipeline(
    *,
    config: AlgorithmConfig,
    registry: ModelRegistry,
) -> FacePipeline:
    """Assemble only the six model roles required for face usability checks."""

    handles = {
        name: registry.get_with_evidence(name)
        for name in _FACE_QUALITY_MODEL_NAMES
    }
    sessions = {name: handle.instance for name, handle in handles.items()}
    evidence = {name: handle.evidence for name, handle in handles.items()}
    versions = {name: handle.evidence.version for name, handle in handles.items()}
    return FacePipeline(
        rotator=RotationAdapter(sessions["rotation_classifier"]),
        detector=FaceBoxesAdapter(sessions["face_detector"]),
        landmarker=Landmark5Adapter(
            sessions["face_landmark_primary"],
            sessions["face_landmark_secondary"],
        ),
        aligner=FaceAlignAdapter(),
        pose_estimator=PoseAdapter(
            sessions["pose_var"],
            sessions["pose_conv"],
        ),
        embedder=_ForbiddenPortraitEmbedder(),
        model_versions=versions,
        config_version=config.config_version,
        max_image_pixels=config.resources.max_image_pixels,
        model_evidence=evidence,
    )


def build_owned_face_pipeline(
    *,
    config: AlgorithmConfig,
    registry: ModelRegistry,
) -> FacePipeline:
    """Build a pipeline from sessions isolated from the caller's registry."""

    if not isinstance(registry, ModelRegistry):
        raise TypeError("registry must be a ModelRegistry")
    isolated_registry = ModelRegistry.spawn_isolated(
        registry,
        _FACE_PIPELINE_MODEL_NAMES,
    )
    return build_face_pipeline(config=config, registry=isolated_registry)


def build_owned_face_quality_pipeline(
    *,
    config: AlgorithmConfig,
    registry: ModelRegistry,
) -> FacePipeline:
    """Build a quality-only pipeline isolated from the caller's registry."""

    if not isinstance(registry, ModelRegistry):
        raise TypeError("registry must be a ModelRegistry")
    isolated_registry = ModelRegistry.spawn_isolated(
        registry,
        _FACE_QUALITY_MODEL_NAMES,
    )
    return build_face_quality_pipeline(config=config, registry=isolated_registry)


_default_lock = threading.Lock()
_default_runtime: tuple[FacePipeline, AlgorithmConfig] | None = None


def get_default_runtime() -> tuple[FacePipeline, AlgorithmConfig]:
    """Lazily construct one atomic pipeline/config snapshot per process."""

    global _default_runtime
    if _default_runtime is None:
        with _default_lock:
            if _default_runtime is None:
                config = load_config()
                registry = create_model_registry(
                    _PROJECT_ROOT / "models.manifest.json",
                    config,
                )
                _default_runtime = (
                    build_face_pipeline(
                        config=config,
                        registry=registry,
                    ),
                    config,
                )
    return _default_runtime


def get_default_pipeline() -> FacePipeline:
    """Return the pipeline from the process-wide runtime snapshot."""

    return get_default_runtime()[0]
