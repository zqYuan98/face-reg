"""Runtime ownership adapters; business workflow does not belong here."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from face_algorithm.core.adapters import create_model_registry
from face_algorithm.portrait import build_portrait_generator
from face_algorithm.runtime.calibration import (
    CalibrationLoadError,
    TrustedCalibrationKeyStore,
)
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import ModelRegistry
from face_algorithm.verification import build_image_verification_runtime
from face_algorithm.video import build_video_verification_runtime
from face_algorithm.witness import (
    WITNESS_FACE_MODEL_NAMES,
    build_witness_blur_runtime,
)


class AlgorithmDeadlineExceeded(RuntimeError):
    pass


class DefaultAlgorithmServices:
    """The sole process owner for lazy algorithm runtimes and model health."""

    def __init__(
        self,
        *,
        config: AlgorithmConfig,
        registry: ModelRegistry,
        calibration_key_store: TrustedCalibrationKeyStore | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._calibration_key_store = calibration_key_store
        self._lock = threading.RLock()
        self._health: dict[str, Any] | None = None
        self._portrait = None
        self._witness = None
        self._image_verifier = None
        self._video_verifier = None
        self._video_presence = None
        self._legacy = None

    @classmethod
    def from_config(
        cls,
        *,
        config: AlgorithmConfig,
        project_root: Path,
        calibration_key_store: TrustedCalibrationKeyStore | None = None,
    ) -> "DefaultAlgorithmServices":
        return cls(
            config=config,
            registry=create_model_registry(project_root / "models.manifest.json", config),
            calibration_key_store=calibration_key_store,
        )

    def start(self) -> None:
        image_mode = self._config.image_verification.mode
        video_mode = self._config.video_verification.calibration.mode
        if (
            image_mode == "artifact" or video_mode == "artifact"
        ) and self._calibration_key_store is None:
            raise CalibrationLoadError(
                "artifact calibration requires an independent trust store"
            )
        try:
            health = self._registry.health()
            self._health = health.model_dump(mode="json")
            if image_mode == "artifact":
                self._image_runtime()
            if video_mode == "artifact":
                self._video_runtime()
        except Exception:
            self._health = None
            self._witness = None
            self._image_verifier = None
            self._video_verifier = None
            self._video_presence = None
            raise
        finally:
            self._release_source_registry_instances()

    def close(self) -> None:
        with self._lock:
            self._portrait = None
            self._witness = None
            self._image_verifier = None
            self._video_verifier = None
            self._video_presence = None
            self._legacy = None
            self._health = None
            self._release_source_registry_instances()

    def _release_source_registry_instances(self) -> None:
        """Drop registry probe/source owners after immutable runtimes snapshot them."""

        for entry in self._registry.manifest.models:
            self._registry.reset(entry.name)

    def readiness(self) -> dict[str, Any]:
        health = self._health
        if health is None:
            return {
                "status": "unhealthy",
                "ready": False,
                "models": [],
                "capabilities": {},
            }
        status = str(health["status"])
        model_states = {item["name"]: item["state"] for item in health["models"]}
        core_ready = all(
            model_states.get(name) == "ready"
            for name in (
                "face_detector",
                "face_landmark_primary",
                "face_landmark_secondary",
                "face_recognizer",
                "rotation_classifier",
                "pose_var",
                "pose_conv",
            )
        )
        portrait_ready = (
            self._config.models.portrait_matting.enabled
            and model_states.get("portrait_matting") == "ready"
        )
        witness_face_ready = all(
            model_states.get(name) == "ready"
            for name in WITNESS_FACE_MODEL_NAMES
        )
        return {
            "status": status,
            "ready": status in {"healthy", "degraded"} and core_ready,
            "models": health["models"],
            "capabilities": {
                "portrait": "ready" if portrait_ready else "unavailable",
                "image_verification": "ready" if core_ready else "unavailable",
                "video_verification": "ready" if core_ready else "unavailable",
                "video_presence": "ready" if core_ready else "unavailable",
                "witness_blur": (
                    "ready"
                    if witness_face_ready and portrait_ready
                    else "unavailable"
                ),
            },
        }

    @staticmethod
    def _check_deadline(deadline_monotonic: float) -> None:
        if time.monotonic() >= deadline_monotonic:
            raise AlgorithmDeadlineExceeded("algorithm deadline exceeded")

    def _portrait_runtime(self):
        with self._lock:
            if self._portrait is None:
                try:
                    runtime = build_portrait_generator(
                        config=self._config,
                        registry=self._registry,
                    )
                finally:
                    self._release_source_registry_instances()
                self._portrait = runtime
            return self._portrait

    def _image_runtime(self):
        with self._lock:
            if self._image_verifier is None:
                try:
                    runtime = build_image_verification_runtime(
                        config=self._config,
                        registry=self._registry,
                        key_store=self._calibration_key_store,
                    )
                finally:
                    self._release_source_registry_instances()
                self._image_verifier = runtime
            return self._image_verifier

    def _witness_runtime(self):
        with self._lock:
            if self._witness is None:
                try:
                    runtime = build_witness_blur_runtime(
                        config=self._config,
                        registry=self._registry,
                    )
                finally:
                    self._release_source_registry_instances()
                self._witness = runtime
            return self._witness

    def _video_runtime(self):
        with self._lock:
            if self._video_verifier is None:
                try:
                    runtime = build_video_verification_runtime(
                        config=self._config,
                        registry=self._registry,
                        key_store=self._calibration_key_store,
                    )
                finally:
                    self._release_source_registry_instances()
                self._video_verifier = runtime
            return self._video_verifier

    def _presence_runtime(self):
        with self._lock:
            if self._video_presence is None:
                from face_algorithm.presence import build_video_presence_runtime

                try:
                    runtime = build_video_presence_runtime(
                        config=self._config,
                        registry=self._registry,
                    )
                finally:
                    self._release_source_registry_instances()
                self._video_presence = runtime
            return self._video_presence

    def _legacy_runtime(self):
        with self._lock:
            if self._legacy is None:
                from face_algorithm.core.adapters import build_face_pipeline
                from face_feature_extractor import FaceFeatureExtractor

                try:
                    runtime = FaceFeatureExtractor(
                        pipeline=build_face_pipeline(
                            config=self._config,
                            registry=self._registry,
                        ),
                        profile=self._config.quality_profiles.entry_verify,
                    )
                finally:
                    self._release_source_registry_instances()
                self._legacy = runtime
            return self._legacy

    def generate_portrait(self, image: np.ndarray, *, deadline_monotonic: float):
        self._check_deadline(deadline_monotonic)
        result = self._portrait_runtime().generate(image)
        self._check_deadline(deadline_monotonic)
        return result

    def verify_images(
        self,
        reference_image: np.ndarray,
        probe_image: np.ndarray,
        *,
        deadline_monotonic: float,
    ):
        self._check_deadline(deadline_monotonic)
        result = self._image_runtime().verify(reference_image, probe_image)
        self._check_deadline(deadline_monotonic)
        return result

    def blur_witness_background(
        self,
        image: np.ndarray,
        *,
        deadline_monotonic: float,
    ):
        self._check_deadline(deadline_monotonic)
        result = self._witness_runtime().blur(image)
        self._check_deadline(deadline_monotonic)
        return result

    def verify_video(
        self,
        reference_image: np.ndarray,
        video_path: Path,
        *,
        mode: str,
        deadline_monotonic: float,
    ):
        self._check_deadline(deadline_monotonic)
        runtime = self._video_runtime()
        if mode == "quick":
            return runtime.quick_verify(
                reference_image,
                video_path,
                deadline_monotonic=deadline_monotonic,
            )
        if mode == "full":
            return runtime.full_verify(
                reference_image,
                video_path,
                deadline_monotonic=deadline_monotonic,
            )
        raise ValueError("video mode must be quick or full")

    def verify_video_presence(
        self,
        reference_image: np.ndarray,
        video_path: Path,
        *,
        deadline_monotonic: float,
    ):
        self._check_deadline(deadline_monotonic)
        result = self._presence_runtime().verify(
            reference_image,
            video_path,
            deadline_monotonic=deadline_monotonic,
        )
        self._check_deadline(deadline_monotonic)
        return result

    def detect_faces(self, image: np.ndarray):
        return self._legacy_runtime().detect_faces(image)

    def extract_feature(self, image: np.ndarray):
        result = self._legacy_runtime().extract_features(
            image,
            return_all_faces=False,
            quality_filter=True,
        )
        if not result.success or not result.faces:
            return None
        feature = result.faces[0].feature
        return None if feature is None else feature.copy()
