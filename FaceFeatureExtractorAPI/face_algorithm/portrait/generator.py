"""Production-owned orchestration for standard white-background portraits."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import cv2
import numpy as np

from face_algorithm.core.adapters import build_owned_face_quality_pipeline
from face_algorithm.core.contracts import (
    ImageTooLargeError,
    InvalidImageError,
    validate_image_input,
)
from face_algorithm.core.pipeline import FaceQualityAnalysis
from face_algorithm.portrait.contracts import PortraitResult
from face_algorithm.portrait.runtime import build_portrait_matting_runtime
from face_algorithm.portrait.template import (
    _PortraitGenerationError,
    _generate_portrait_pixels,
)
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import ModelRegistry
from face_algorithm.schemas import ReasonCode, Suggestion

__all__ = [
    "PortraitGeneratorRuntime",
    "PortraitResult",
    "build_portrait_generator",
    "encode_portrait_image",
]


@dataclass(frozen=True, slots=True, init=False)
class PortraitGeneratorRuntime:
    """Immutable operation surface backed by privately owned model instances."""

    model_versions: Mapping[str, str]
    config_version: str
    _analyze_quality: Callable[[np.ndarray, object], FaceQualityAnalysis] = field(
        repr=False,
        compare=False,
    )
    _matte: Callable[[np.ndarray], np.ndarray] = field(repr=False, compare=False)
    _profile: object = field(repr=False, compare=False)
    _template: object = field(repr=False, compare=False)
    _max_image_pixels: int = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise TypeError("use build_portrait_generator")

    @classmethod
    def _from_builder(
        cls,
        *,
        face_pipeline: object,
        matting_runtime: object,
        config: AlgorithmConfig,
    ) -> PortraitGeneratorRuntime:
        analyze = getattr(face_pipeline, "analyze_quality", None)
        matte = getattr(matting_runtime, "matte", None)
        if not callable(analyze):
            raise TypeError("face pipeline must provide analyze_quality")
        if not callable(matte):
            raise TypeError("matting runtime must provide matte")
        model_info = getattr(matting_runtime, "model_info", None)
        version = getattr(model_info, "version", None)
        if not isinstance(version, str) or not version:
            raise TypeError("matting runtime must provide safe model metadata")
        versions = dict(getattr(face_pipeline, "model_versions", {}))
        versions["portrait_matting"] = version
        instance = object.__new__(cls)
        object.__setattr__(instance, "model_versions", MappingProxyType(versions))
        object.__setattr__(instance, "config_version", config.config_version)
        object.__setattr__(instance, "_analyze_quality", analyze)
        object.__setattr__(instance, "_matte", matte)
        object.__setattr__(instance, "_profile", config.quality_profiles.portrait)
        object.__setattr__(instance, "_template", config.portrait_template)
        object.__setattr__(
            instance,
            "_max_image_pixels",
            config.resources.max_image_pixels,
        )
        return instance

    def generate(self, image: object) -> PortraitResult:
        """Generate one derived portrait or a structured recapture/review result."""

        started = time.perf_counter()
        trace_id = uuid.uuid4().hex
        original_width: int | None = None
        original_height: int | None = None
        rotation_quarter_turns = 0

        def finish(
            *,
            suggestion: Suggestion,
            reasons: tuple[ReasonCode, ...],
            generated: object | None = None,
        ) -> PortraitResult:
            return PortraitResult(
                success=generated is not None,
                suggestion=suggestion,
                reason_codes=reasons,
                model_versions=self.model_versions,
                config_version=self.config_version,
                processing_time_ms=max(
                    0.0,
                    (time.perf_counter() - started) * 1000.0,
                ),
                trace_id=trace_id,
                template_version=self._template.version,
                output_width=int(self._template.output_width),
                output_height=int(self._template.output_height),
                output_format=str(self._template.output_format),
                jpeg_quality=int(self._template.jpeg_quality),
                original_width=original_width,
                original_height=original_height,
                rotation_quarter_turns=rotation_quarter_turns,
                placement=(None if generated is None else generated.placement),
                matting_quality=(
                    None if generated is None else generated.matting_quality
                ),
                image=None if generated is None else generated.image,
            )

        try:
            original_height, original_width = validate_image_input(
                image,
                self._max_image_pixels,
            )
        except InvalidImageError:
            return finish(
                suggestion=Suggestion.RECAPTURE,
                reasons=(ReasonCode.INVALID_IMAGE,),
            )
        except ImageTooLargeError as error:
            original_height, original_width = error.height, error.width
            return finish(
                suggestion=Suggestion.RECAPTURE,
                reasons=(ReasonCode.IMAGE_TOO_LARGE,),
            )

        try:
            analysis = self._analyze_quality(image, self._profile)
        except Exception:
            return finish(
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
            )
        if not isinstance(analysis, FaceQualityAnalysis):
            return finish(
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
            )
        rotation_quarter_turns = analysis.public.rotation_quarter_turns
        if (
            analysis.public.suggestion is not Suggestion.PASS
            or not analysis.public.success
            or analysis.public.reason_codes
        ):
            suggestion = analysis.public.suggestion
            if suggestion is Suggestion.PASS:
                suggestion = Suggestion.REVIEW
            reasons = analysis.public.reason_codes or (ReasonCode.INFERENCE_ERROR,)
            return finish(suggestion=suggestion, reasons=reasons)
        if (
            analysis.corrected_image is None
            or analysis.public.box is None
            or analysis.public.landmarks is None
        ):
            return finish(
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
            )
        corrected = analysis.corrected_image
        try:
            validate_image_input(corrected, self._max_image_pixels)
        except ValueError:
            return finish(
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
            )

        required_face_height = int(
            np.ceil(
                float(self._template.output_height)
                * float(self._template.face_height_ratio)
                / float(self._template.max_upscale_ratio)
            )
        )
        minimum_face_width = float(self._profile.resolution.width)
        minimum_face_height = float(
            max(self._profile.resolution.height, required_face_height)
        )
        if (
            analysis.public.box.width < minimum_face_width
            or analysis.public.box.height < minimum_face_height
        ):
            return finish(
                suggestion=Suggestion.RECAPTURE,
                reasons=(ReasonCode.FACE_TOO_SMALL,),
            )

        try:
            matte = self._matte(corrected)
            if not (
                isinstance(matte, np.ndarray)
                and matte.dtype == np.float32
                and matte.shape == corrected.shape[:2]
                and matte.flags.c_contiguous
                and np.all(np.isfinite(matte))
                and float(matte.min()) >= 0.0
                and float(matte.max()) <= 1.0
            ):
                raise TypeError("invalid portrait matte")
        except Exception:
            return finish(
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.MATTING_FAILED,),
            )

        try:
            generated = _generate_portrait_pixels(
                corrected,
                matte,
                analysis.public.box,
                analysis.public.landmarks,
                self._template,
            )
        except _PortraitGenerationError as error:
            return finish(
                suggestion=Suggestion.RECAPTURE,
                reasons=(error.reason_code,),
            )
        except Exception:
            return finish(
                suggestion=Suggestion.REVIEW,
                reasons=(ReasonCode.INFERENCE_ERROR,),
            )
        return finish(
            suggestion=Suggestion.PASS,
            reasons=(),
            generated=generated,
        )


def _bind_portrait_generator(
    *,
    face_pipeline: object,
    matting_runtime: object,
    config: AlgorithmConfig,
) -> PortraitGeneratorRuntime:
    """Private construction seam for deterministic unit tests."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be an AlgorithmConfig")
    return PortraitGeneratorRuntime._from_builder(
        face_pipeline=face_pipeline,
        matting_runtime=matting_runtime,
        config=config,
    )


def build_portrait_generator(
    *,
    config: AlgorithmConfig,
    registry: ModelRegistry,
) -> PortraitGeneratorRuntime:
    """Build a portrait runtime that owns isolated face and matting models."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be an AlgorithmConfig")
    if not isinstance(registry, ModelRegistry):
        raise TypeError("registry must be a ModelRegistry")
    matting_runtime = build_portrait_matting_runtime(
        config=config,
        registry=registry,
    )
    face_pipeline = build_owned_face_quality_pipeline(
        config=config,
        registry=registry,
    )
    return _bind_portrait_generator(
        face_pipeline=face_pipeline,
        matting_runtime=matting_runtime,
        config=config,
    )


def encode_portrait_image(
    result: PortraitResult,
    *,
    output_format: Literal["jpeg", "png"] | None = None,
    jpeg_quality: int | None = None,
) -> bytes:
    """Encode a successful core image without changing the original evidence."""

    if not isinstance(result, PortraitResult) or not result.success or result.image is None:
        raise ValueError("only a successful portrait result can be encoded")
    selected_format = result.output_format if output_format is None else output_format
    selected_quality = result.jpeg_quality if jpeg_quality is None else jpeg_quality
    if selected_format == "png":
        extension = ".png"
        options: list[int] = []
    elif selected_format == "jpeg":
        if isinstance(selected_quality, bool) or not isinstance(selected_quality, int):
            raise TypeError("jpeg_quality must be an integer")
        if not 1 <= selected_quality <= 100:
            raise ValueError("jpeg_quality must be in [1, 100]")
        extension = ".jpg"
        options = [cv2.IMWRITE_JPEG_QUALITY, selected_quality]
    else:
        raise ValueError("output_format must be jpeg or png")
    encoded, buffer = cv2.imencode(extension, result.image, options)
    if not encoded:
        raise RuntimeError("portrait encoding failed")
    return bytes(buffer)
