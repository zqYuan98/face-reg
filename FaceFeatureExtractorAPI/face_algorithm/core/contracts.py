"""Shared fail-closed contracts for decoded images and face embeddings."""

from __future__ import annotations

from typing import Any

import numpy as np

from face_algorithm.schemas import FEATURE_DIMENSION, FeatureVersion

__all__ = [
    "FeatureContractError",
    "FeatureVersionMismatchError",
    "ImageTooLargeError",
    "InvalidImageError",
    "extract_normalized_embedding",
    "validate_image_input",
]


class InvalidImageError(ValueError):
    """The decoded input is not a non-empty uint8 BGR image."""


class ImageTooLargeError(ValueError):
    """The decoded image exceeds the configured pixel limit."""

    def __init__(self, *, height: int, width: int, max_image_pixels: int) -> None:
        self.height = height
        self.width = width
        self.max_image_pixels = max_image_pixels
        super().__init__(
            f"image exceeds max_image_pixels={max_image_pixels}: "
            f"{height}x{width}={height * width}"
        )


class FeatureContractError(ValueError):
    """The recognizer returned an invalid feature payload."""


class FeatureVersionMismatchError(FeatureContractError):
    """The recognizer metadata or output dimension is not the v1 protocol."""


def validate_image_input(
    image: object,
    max_image_pixels: int,
) -> tuple[int, int]:
    """Validate one decoded BGR image and return ``(height, width)``."""

    if not (
        isinstance(image, np.ndarray)
        and image.dtype == np.uint8
        and image.ndim == 3
        and image.shape[2] == 3
        and image.shape[0] > 0
        and image.shape[1] > 0
        and image.size > 0
    ):
        raise InvalidImageError("image must be a non-empty uint8 HxWx3 array")
    height, width = map(int, image.shape[:2])
    if height * width > max_image_pixels:
        raise ImageTooLargeError(
            height=height,
            width=width,
            max_image_pixels=max_image_pixels,
        )
    return height, width


def extract_normalized_embedding(embedder: Any, aligned_face: np.ndarray) -> np.ndarray:
    """Run the v1 recognizer and return a verified float32 unit vector."""

    if (
        getattr(embedder, "feature_version", None)
        != FeatureVersion.RECOGNIZER_V1_1024.value
        or getattr(embedder, "feature_dim", None) != FEATURE_DIMENSION
    ):
        raise FeatureVersionMismatchError("recognizer metadata is not v1/1024")

    raw_embedding = embedder.extract(aligned_face)
    if not isinstance(raw_embedding, np.ndarray):
        raise FeatureContractError("recognizer output must be an ndarray")
    if raw_embedding.shape != (FEATURE_DIMENSION,):
        if raw_embedding.size == 512:
            raise FeatureVersionMismatchError("recognizer output dimension is 512")
        raise FeatureContractError("recognizer output shape must be (1024,)")
    if raw_embedding.dtype != np.float32:
        raise FeatureContractError("recognizer output dtype must be float32")

    embedding64 = raw_embedding.astype(np.float64, copy=True)
    if not np.all(np.isfinite(embedding64)):
        raise FeatureContractError("recognizer output must be finite")
    norm = float(np.linalg.norm(embedding64))
    if not np.isfinite(norm) or norm <= 0.0:
        raise FeatureContractError("recognizer output norm must be finite and nonzero")

    embedding = (embedding64 / norm).astype(np.float32)
    normalized_norm = float(np.linalg.norm(embedding.astype(np.float64)))
    if (
        not np.all(np.isfinite(embedding))
        or not np.isfinite(normalized_norm)
        or normalized_norm <= 0.0
        or not np.isclose(normalized_norm, 1.0, rtol=1e-5, atol=1e-6)
    ):
        raise FeatureContractError("normalized recognizer output is invalid")
    return embedding
