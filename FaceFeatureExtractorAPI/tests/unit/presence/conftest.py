from pathlib import Path

import numpy as np
import pytest

from face_algorithm.core.pipeline import FacePipeline
from face_algorithm.core.protocols import RotationResult
from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_registry import VerifiedModelEvidence
from face_algorithm.schemas import FaceBox, Pose


def textured(marker=0):
    y, x = np.indices((400, 400))
    base = (96 + ((x * 17 + y * 29) % 64)).astype(np.uint8)
    image = np.stack((base, np.roll(base, 3, axis=1), np.roll(base, 5, axis=0)), axis=2)
    image[0, 0, 0] = marker
    return image


def box(x=100, y=100, size=160):
    return FaceBox(x1=float(x), y1=float(y), x2=float(x + size), y2=float(y + size))


class Rotator:
    def correct(self, image):
        return RotationResult(corrected_image=image.copy(), quarter_turns=0)


class Detector:
    def __init__(self, mapping):
        self.mapping = mapping

    def detect(self, image):
        return self.mapping.get(int(image[0, 0, 0]), (box(),))


class Landmarker:
    def __init__(self, missing=False, edge=False):
        self.missing, self.edge = missing, edge

    def detect(self, image, face):
        points = np.array([(0.25, 0.3), (0.75, 0.3), (0.5, 0.52), (0.32, 0.75), (0.68, 0.75)], dtype=np.float32)
        points *= (face.width, face.height)
        points += (face.x1, face.y1)
        if self.missing:
            points = points[:4]
        if self.edge:
            points[0, 0] = 0
        return points


class Aligner:
    def align(self, image, landmarks):
        aligned = textured()[:256, :256].copy()
        # Test identity: reference or right-hand candidate; not detector rank.
        aligned[0, 0, 0] = 0 if image[0, 0, 0] == 0 or landmarks[2, 0] > 220 else 1
        return aligned


class PoseEstimator:
    def __init__(self, yaw=0.0):
        self.yaw = yaw
        self.inputs = []

    def estimate(self, image, face):
        self.inputs.append((image.shape, face))
        return Pose(yaw=float(self.yaw), pitch=0.0, roll=0.0)


class Embedder:
    feature_version = "recognizer_v1_1024"
    feature_dim = 1024

    def __init__(self, invalid=None):
        self.invalid = invalid

    def extract(self, aligned):
        if self.invalid is not None:
            return self.invalid
        vector = np.zeros(1024, dtype=np.float32)
        vector[int(aligned[0, 0, 0])] = 1.0
        return vector


@pytest.fixture
def config():
    return load_config(Path(__file__).resolve().parents[3] / "config" / "algorithm.yaml")


@pytest.fixture
def pipeline_factory(config):
    def build(mapping=None, landmarker=None, pose=None, embedder=None, aligner=None):
        roles = ("face_detector", "face_landmark_primary", "face_landmark_secondary", "face_recognizer", "rotation_classifier", "pose_var", "pose_conv")
        evidence = {}
        for role in roles:
            expected = getattr(config.models, role)
            evidence[role] = VerifiedModelEvidence(
                name=role, version=expected.version, sha256=expected.sha256, size=100,
                backend=expected.backend, providers=expected.providers,
                feature_version="recognizer_v1_1024" if role == "face_recognizer" else None,
                output_dimension=1024 if role == "face_recognizer" else None,
            )
        return FacePipeline(
            rotator=Rotator(), detector=Detector(mapping or {}), landmarker=landmarker or Landmarker(),
            aligner=aligner or Aligner(), pose_estimator=pose or PoseEstimator(), embedder=embedder or Embedder(),
            model_versions={key: value.version for key, value in evidence.items()},
            model_evidence=evidence, config_version=config.config_version,
            max_image_pixels=config.resources.max_image_pixels,
        )
    return build
