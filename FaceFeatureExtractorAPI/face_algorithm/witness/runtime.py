"""Owned witness-photo runtime orchestrating quality, matting, cards and blur."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType

import numpy as np

from face_algorithm.core.adapters import build_owned_face_quality_pipeline
from face_algorithm.core.contracts import (
    ImageTooLargeError,
    InvalidImageError,
    validate_image_input,
)
from face_algorithm.core.pipeline import FaceQualityAnalysis
from face_algorithm.portrait.runtime import build_portrait_matting_runtime
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import ModelRegistry
from face_algorithm.schemas import CardRegion, LandmarkPoint, ReasonCode, Suggestion
from face_algorithm.witness.blur import (
    _WitnessBlurError,
    _blur_witness_pixels,
    _select_witness_subject_matte,
)
from face_algorithm.witness.card_detector import (
    _CardDetectionResult,
    _detect_card_regions,
)
from face_algorithm.witness.contracts import WitnessBlurResult

__all__ = ["WitnessBlurRuntime", "WitnessBlurResult", "build_witness_blur_runtime"]


@dataclass(frozen=True, slots=True, init=False)
class WitnessBlurRuntime:
    """Immutable operation surface backed by privately owned model instances."""

    model_versions: Mapping[str, str]
    config_version: str
    _analyze_quality: Callable[[np.ndarray, object], FaceQualityAnalysis] = field(
        repr=False, compare=False
    )
    _matte: Callable[[np.ndarray], np.ndarray] = field(repr=False, compare=False)
    _detect_cards: Callable[..., _CardDetectionResult] = field(
        repr=False, compare=False
    )
    _profile: object = field(repr=False, compare=False)
    _options: object = field(repr=False, compare=False)
    _max_image_pixels: int = field(repr=False, compare=False)
    _request_lock: object = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise TypeError("use build_witness_blur_runtime")

    @classmethod
    def _from_builder(
        cls,
        *,
        face_pipeline: object,
        matting_runtime: object,
        card_detector: object,
        config: AlgorithmConfig,
    ) -> "WitnessBlurRuntime":
        analyze = getattr(face_pipeline, "analyze_quality", None)
        matte = getattr(matting_runtime, "matte", None)
        if not callable(analyze):
            raise TypeError("face pipeline must provide analyze_quality")
        if not callable(matte):
            raise TypeError("matting runtime must provide matte")
        if not callable(card_detector):
            raise TypeError("card detector must be callable")
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
        object.__setattr__(instance, "_detect_cards", card_detector)
        object.__setattr__(instance, "_profile", config.quality_profiles.witness)
        object.__setattr__(instance, "_options", config.witness)
        object.__setattr__(
            instance,
            "_max_image_pixels",
            min(
                config.resources.max_image_pixels,
                config.witness.max_witness_pixels,
            ),
        )
        object.__setattr__(instance, "_request_lock", Lock())
        return instance

    def blur(self, image: object) -> WitnessBlurResult:
        with self._request_lock:
            return self._blur_once(image)

    def _blur_once(self, image: object) -> WitnessBlurResult:
        started = time.perf_counter()
        trace_id = uuid.uuid4().hex
        original_width: int | None = None
        original_height: int | None = None
        turns = 0
        candidate_count = 0

        def finish(
            suggestion: Suggestion,
            reasons: tuple[ReasonCode, ...],
            *,
            cards: tuple[CardRegion, ...] = (),
            quality=None,
            summary=None,
            output: np.ndarray | None = None,
        ) -> WitnessBlurResult:
            restored_cards = (
                _restore_card_regions(cards, turns, original_width, original_height)
                if output is not None
                else ()
            )
            restored_output = (
                _restore_image_orientation(output, turns)
                if output is not None
                else None
            )
            return WitnessBlurResult(
                success=restored_output is not None,
                suggestion=suggestion,
                reason_codes=reasons,
                model_versions=self.model_versions,
                config_version=self.config_version,
                processing_time_ms=max(
                    0.0, (time.perf_counter() - started) * 1000.0
                ),
                trace_id=trace_id,
                original_width=original_width,
                original_height=original_height,
                rotation_quarter_turns=turns,
                coordinate_space="original_image",
                card_regions=restored_cards,
                card_candidate_count=candidate_count,
                protected_area_ratio=(
                    0.0 if summary is None else summary.protected_area_ratio
                ),
                matting_quality=quality,
                blur_summary=summary,
                image=restored_output,
            )

        try:
            original_height, original_width = validate_image_input(
                image, self._max_image_pixels
            )
        except InvalidImageError:
            return finish(Suggestion.RECAPTURE, (ReasonCode.INVALID_IMAGE,))
        except ImageTooLargeError as error:
            original_height, original_width = error.height, error.width
            return finish(Suggestion.RECAPTURE, (ReasonCode.IMAGE_TOO_LARGE,))

        try:
            analysis = self._analyze_quality(image, self._profile)
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        if not isinstance(analysis, FaceQualityAnalysis):
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        turns = analysis.public.rotation_quarter_turns
        if (
            not analysis.public.success
            or analysis.public.suggestion is not Suggestion.PASS
            or analysis.public.reason_codes
        ):
            suggestion = analysis.public.suggestion
            if suggestion is Suggestion.PASS:
                suggestion = Suggestion.REVIEW
            reasons = analysis.public.reason_codes or (ReasonCode.INFERENCE_ERROR,)
            return finish(suggestion, reasons)
        corrected = analysis.corrected_image
        face = analysis.public.box
        if corrected is None or face is None or analysis.public.landmarks is None:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        try:
            validate_image_input(corrected, self._max_image_pixels)
        except ValueError:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))

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
            return finish(Suggestion.REVIEW, (ReasonCode.MATTING_FAILED,))
        try:
            subject, quality = _select_witness_subject_matte(
                matte, face, self._options
            )
        except _WitnessBlurError as error:
            return finish(Suggestion.RECAPTURE, (error.reason_code,))
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.MATTING_FAILED,))

        try:
            detection = self._detect_cards(
                corrected, face, subject, self._options
            )
            if not isinstance(detection, _CardDetectionResult):
                raise TypeError("invalid card detection result")
            candidate_count = detection.candidate_count
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        if detection.reason_code is not None:
            return finish(Suggestion.RECAPTURE, (detection.reason_code,))
        try:
            output, summary = _blur_witness_pixels(
                corrected, subject, detection.cards, self._options
            )
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        return finish(
            Suggestion.PASS,
            (),
            cards=detection.cards,
            quality=quality,
            summary=summary,
            output=output,
        )


def _restore_image_orientation(image: np.ndarray, turns: int) -> np.ndarray:
    if turns == 0:
        return np.ascontiguousarray(image)
    if turns not in (1, 2, 3):
        raise ValueError("invalid rotation quarter turns")
    return np.ascontiguousarray(np.rot90(image, k=turns))


def _restore_card_regions(
    cards: tuple[CardRegion, ...],
    turns: int,
    original_width: int | None,
    original_height: int | None,
) -> tuple[CardRegion, ...]:
    if original_width is None or original_height is None:
        raise ValueError("original image dimensions unavailable")

    def restore(point: LandmarkPoint) -> LandmarkPoint:
        x, y = point.x, point.y
        if turns == 0:
            restored_x, restored_y = x, y
        elif turns == 1:
            restored_x, restored_y = y, original_height - 1.0 - x
        elif turns == 2:
            restored_x = original_width - 1.0 - x
            restored_y = original_height - 1.0 - y
        elif turns == 3:
            restored_x, restored_y = original_width - 1.0 - y, x
        else:
            raise ValueError("invalid rotation quarter turns")
        return LandmarkPoint(
            x=float(np.clip(restored_x, 0.0, original_width - 1.0)),
            y=float(np.clip(restored_y, 0.0, original_height - 1.0)),
        )

    return tuple(
        CardRegion(
            corners=tuple(restore(point) for point in card.corners),
            confidence=card.confidence,
        )
        for card in cards
    )


def _bind_witness_blur_runtime(
    *,
    face_pipeline: object,
    matting_runtime: object,
    card_detector: object,
    config: AlgorithmConfig,
) -> WitnessBlurRuntime:
    """Private dependency seam used only by deterministic unit tests."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be an AlgorithmConfig")
    return WitnessBlurRuntime._from_builder(
        face_pipeline=face_pipeline,
        matting_runtime=matting_runtime,
        card_detector=card_detector,
        config=config,
    )


def build_witness_blur_runtime(
    *, config: AlgorithmConfig, registry: ModelRegistry
) -> WitnessBlurRuntime:
    """Build a runtime owning isolated six-model quality and MODNet graphs."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be an AlgorithmConfig")
    if not isinstance(registry, ModelRegistry):
        raise TypeError("registry must be a ModelRegistry")
    matting_runtime = build_portrait_matting_runtime(
        config=config, registry=registry
    )
    face_pipeline = build_owned_face_quality_pipeline(
        config=config, registry=registry
    )
    return _bind_witness_blur_runtime(
        face_pipeline=face_pipeline,
        matting_runtime=matting_runtime,
        card_detector=_detect_card_regions,
        config=config,
    )
