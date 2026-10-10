"""Small algorithm protocols with no web or business-layer dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, TypeAlias, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from face_algorithm.schemas import CardRegion, FaceBox, Pose

Image: TypeAlias = NDArray[np.uint8]
PersonMask: TypeAlias = NDArray[np.float32]
EmbeddingVector: TypeAlias = NDArray[np.float32]
LandmarkArray: TypeAlias = NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class RotationResult:
    corrected_image: Image
    quarter_turns: int


@runtime_checkable
class ImageRotator(Protocol):
    def correct(self, image: Image) -> RotationResult: ...


@runtime_checkable
class FaceDetector(Protocol):
    def detect(self, image: Image) -> Sequence[FaceBox]: ...


@runtime_checkable
class LandmarkDetector(Protocol):
    def detect(self, image: Image, face: FaceBox) -> LandmarkArray: ...


@runtime_checkable
class FaceAligner(Protocol):
    def align(self, image: Image, landmarks: LandmarkArray) -> Image: ...


@runtime_checkable
class FaceEmbedder(Protocol):
    feature_version: str
    feature_dim: int

    def extract(self, aligned_face: Image) -> EmbeddingVector: ...


@runtime_checkable
class PoseEstimator(Protocol):
    def estimate(self, image: Image, face: FaceBox) -> Pose: ...


@runtime_checkable
class PortraitMatting(Protocol):
    def alpha(self, image: Image) -> PersonMask: ...


@runtime_checkable
class CardDetector(Protocol):
    def detect(
        self,
        image: Image,
        person_mask: PersonMask,
    ) -> Sequence[CardRegion]: ...
