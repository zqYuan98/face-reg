"""Public, model-hiding portrait-matting runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from face_algorithm.core.contracts import validate_image_input
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import ModelRegistry, VerifiedModelEvidence

__all__ = [
    "PortraitMattingDisabledError",
    "PortraitMattingDependencyError",
    "PortraitMattingModelInfo",
    "PortraitMattingRuntime",
    "build_portrait_matting_runtime",
]


class PortraitMattingDisabledError(RuntimeError):
    """Portrait matting was not explicitly enabled in this deployment."""


class PortraitMattingDependencyError(RuntimeError):
    """The optional local inference dependency is not installed."""


@dataclass(frozen=True, slots=True)
class PortraitMattingModelInfo:
    """Immutable, non-sensitive evidence about the active matting model."""

    name: str
    version: str
    sha256: str
    size: int
    backend: str
    providers: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class PortraitMattingRuntime:
    """Immutable public operation surface that never returns the torch model."""

    model_info: PortraitMattingModelInfo
    _max_image_pixels: int = field(repr=False, compare=False)
    _matte: Callable[[object], np.ndarray] = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise TypeError("use build_portrait_matting_runtime")

    @classmethod
    def _from_builder(
        cls,
        adapter: object,
        evidence: VerifiedModelEvidence,
        max_image_pixels: int,
    ) -> PortraitMattingRuntime:
        matte = getattr(adapter, "matte", None)
        if not callable(matte):
            raise TypeError("portrait matting adapter must provide matte")
        instance = object.__new__(cls)
        object.__setattr__(
            instance,
            "model_info",
            PortraitMattingModelInfo(
                name=evidence.name,
                version=evidence.version,
                sha256=evidence.sha256,
                size=evidence.size,
                backend=evidence.backend,
                providers=evidence.providers,
            ),
        )
        object.__setattr__(instance, "_max_image_pixels", max_image_pixels)
        object.__setattr__(instance, "_matte", matte)
        return instance

    def matte(self, image: object) -> np.ndarray:
        """Return a contiguous float32 alpha matte in the input resolution."""

        validate_image_input(image, self._max_image_pixels)
        return self._matte(image)


def _validate_evidence(
    evidence: VerifiedModelEvidence,
    config: AlgorithmConfig,
) -> None:
    expected = config.models.portrait_matting
    if (
        evidence.name != "portrait_matting"
        or evidence.version != expected.version
        or evidence.sha256 != expected.sha256
        or evidence.backend != expected.backend
        or evidence.providers != expected.providers
        or evidence.feature_version is not None
        or evidence.output_dimension is not None
    ):
        raise ValueError("portrait matting runtime evidence does not match config")


def _bind_portrait_matting_runtime(
    *,
    handle: object,
    config: AlgorithmConfig,
) -> PortraitMattingRuntime:
    from face_algorithm.portrait.modnet import _ModNetAdapter

    evidence = getattr(handle, "evidence", None)
    instance = getattr(handle, "instance", None)
    if not isinstance(evidence, VerifiedModelEvidence):
        raise TypeError("portrait_matting handle has invalid evidence")
    _validate_evidence(evidence, config)
    if not isinstance(instance, _ModNetAdapter):
        raise TypeError("portrait_matting registry instance has the wrong type")
    return PortraitMattingRuntime._from_builder(
        instance,
        evidence,
        config.resources.max_image_pixels,
    )


def build_portrait_matting_runtime(
    *,
    registry: ModelRegistry,
    config: AlgorithmConfig,
) -> PortraitMattingRuntime:
    """Bind the verified optional MODNet adapter without exposing its model."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be an AlgorithmConfig")
    if not config.models.portrait_matting.enabled:
        raise PortraitMattingDisabledError(
            "portrait_matting must be explicitly enabled before startup"
        )
    try:
        from face_algorithm.portrait.modnet import _ModNetAdapter
    except ModuleNotFoundError as error:
        missing = error.name or ""
        if missing == "torch" or missing.startswith("torch."):
            raise PortraitMattingDependencyError(
                "portrait matting requires: pip install face-algorithm[portrait]"
            ) from None
        raise
    if not isinstance(registry, ModelRegistry):
        raise TypeError("registry must be a ModelRegistry")
    isolated_registry = ModelRegistry.spawn_isolated(
        registry,
        ("portrait_matting",),
    )
    return _bind_portrait_matting_runtime(
        handle=isolated_registry.get_with_evidence("portrait_matting"),
        config=config,
    )
