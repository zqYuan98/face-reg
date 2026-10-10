"""Dependency-injected interfaces for face algorithm implementations."""

from .protocols import (
    CardDetector,
    EmbeddingVector,
    FaceAligner,
    FaceDetector,
    FaceEmbedder,
    Image,
    ImageRotator,
    LandmarkArray,
    LandmarkDetector,
    PersonMask,
    PortraitMatting,
    PoseEstimator,
    RotationResult,
)

__all__ = [
    "CardDetector",
    "EmbeddingVector",
    "FaceAligner",
    "FaceDetector",
    "FaceEmbedder",
    "Image",
    "ImageRotator",
    "LandmarkArray",
    "LandmarkDetector",
    "PersonMask",
    "PortraitMatting",
    "PoseEstimator",
    "RotationResult",
]
