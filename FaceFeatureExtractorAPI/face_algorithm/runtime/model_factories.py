"""Lazy factories for optional model families outside the face core."""

from __future__ import annotations

from collections.abc import Callable

from face_algorithm.runtime.model_registry import VerifiedArtifact

__all__ = ["create_portrait_matting_factory"]


def create_portrait_matting_factory(
    *,
    max_image_pixels: int,
) -> Callable[[VerifiedArtifact], object]:
    """Return a lazy MODNet factory without importing torch at core startup."""

    def create(artifact: VerifiedArtifact) -> object:
        from face_algorithm.portrait import modnet

        return modnet._create_modnet_adapter(
            artifact,
            max_image_pixels=max_image_pixels,
        )

    return create
