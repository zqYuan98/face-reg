"""Owned person-first witness runtime orchestrating face hints, matting and blur."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType

import numpy as np

from face_algorithm.core.adapters import (
    FaceBoxesAdapter,
    Landmark5Adapter,
    RotationAdapter,
)
from face_algorithm.core.contracts import (
    ImageTooLargeError,
    InvalidImageError,
    validate_image_input,
)
from face_algorithm.core.face_selection import (
    FaceCandidateSelector,
    FaceSelectionPreparation,
)
from face_algorithm.core.pipeline import FaceQualityAnalysis
from face_algorithm.portrait.runtime import build_portrait_matting_runtime
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import (
    ModelRegistry,
    VerifiedModelEvidence,
)
from face_algorithm.schemas import (
    CardRegion,
    FaceBox,
    LandmarkPoint,
    ReasonCode,
    Suggestion,
)
from face_algorithm.witness.blur import (
    _blur_witness_pixels,
)
from face_algorithm.witness.card_detector import (
    _CardDetectionResult,
    _detect_card_regions,
)
from face_algorithm.witness.contracts import WitnessBlurResult, _WitnessBlurError
from face_algorithm.witness.protection import _build_witness_protection_mask
from face_algorithm.witness.subject_selector import _analyze_witness_subject

__all__ = [
    "WITNESS_FACE_MODEL_NAMES",
    "WITNESS_REQUIRED_MODEL_NAMES",
    "WitnessBlurRuntime",
    "WitnessBlurResult",
    "build_witness_blur_runtime",
]

WITNESS_FACE_MODEL_NAMES = (
    "rotation_classifier",
    "face_detector",
    "face_landmark_primary",
    "face_landmark_secondary",
)
WITNESS_REQUIRED_MODEL_NAMES = WITNESS_FACE_MODEL_NAMES + ("portrait_matting",)


@dataclass(frozen=True, slots=True)
class _WitnessFaceProbe:
    corrected_image: np.ndarray = field(repr=False, compare=False)
    rotation_quarter_turns: int
    face: FaceBox | None
    reason_code: ReasonCode | None
    finalize: Callable[[tuple[CardRegion, ...]], ReasonCode | None] = field(
        repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class _OwnedWitnessFaceProbe:
    """Privately owned models required by the witness face-only path."""

    rotator: object = field(repr=False, compare=False)
    detector: object = field(repr=False, compare=False)
    landmarker: object = field(repr=False, compare=False)
    selector: FaceCandidateSelector = field(repr=False, compare=False)
    model_versions: Mapping[str, str]
    model_evidence: Mapping[str, VerifiedModelEvidence] = field(
        repr=False, compare=False
    )

    def probe(self, image: np.ndarray, profile: object) -> _WitnessFaceProbe:
        return _probe_with_components(
            image,
            profile,
            rotator=self.rotator,
            detector=self.detector,
            landmarker=self.landmarker,
            selector=self.selector,
        )


@dataclass(frozen=True, slots=True, init=False)
class WitnessBlurRuntime:
    """Immutable operation surface backed by privately owned model instances."""

    model_versions: Mapping[str, str]
    config_version: str
    _probe_face: Callable[[np.ndarray, object], _WitnessFaceProbe] = field(
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
        if not callable(matte):
            raise TypeError("matting runtime must provide matte")
        if not callable(card_detector):
            raise TypeError("card detector must be callable")
        probe_face = _make_witness_face_probe(face_pipeline, analyze)
        model_info = getattr(matting_runtime, "model_info", None)
        version = getattr(model_info, "version", None)
        if not isinstance(version, str) or not version:
            raise TypeError("matting runtime must provide safe model metadata")
        raw_versions = getattr(face_pipeline, "model_versions", None)
        if not isinstance(raw_versions, Mapping) or not raw_versions:
            raise TypeError("face probe must provide executed model versions")
        versions = dict(raw_versions)
        versions["portrait_matting"] = version
        instance = object.__new__(cls)
        object.__setattr__(instance, "model_versions", MappingProxyType(versions))
        object.__setattr__(instance, "config_version", config.config_version)
        object.__setattr__(instance, "_probe_face", probe_face)
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
            probe = self._probe_face(image, self._profile)
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        if not isinstance(probe, _WitnessFaceProbe):
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        turns = probe.rotation_quarter_turns
        if probe.reason_code is not None:
            suggestion = (
                Suggestion.REVIEW
                if probe.reason_code is ReasonCode.INFERENCE_ERROR
                else Suggestion.RECAPTURE
            )
            return finish(suggestion, (probe.reason_code,))
        corrected = probe.corrected_image
        face = probe.face
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
            selection = _analyze_witness_subject(matte, face, self._options)
            subject = selection.matte
            quality = selection.quality
            del matte, selection
        except _WitnessBlurError as error:
            return finish(Suggestion.RECAPTURE, (error.reason_code,))
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.MATTING_FAILED,))

        detection: _CardDetectionResult | None = None
        if face is not None:
            try:
                detected = self._detect_cards(
                    corrected, face, subject, self._options
                )
                if not isinstance(detected, _CardDetectionResult):
                    raise TypeError("invalid card detection result")
                if (
                    isinstance(detected.candidate_count, bool)
                    or not isinstance(detected.candidate_count, int)
                    or detected.candidate_count < 0
                    or detected.candidate_count < len(detected.cards)
                ):
                    raise TypeError("invalid card candidate count")
                if not (
                    isinstance(detected.cards, tuple)
                    and isinstance(detected.protection_cards, tuple)
                    and all(isinstance(card, CardRegion) for card in detected.cards)
                    and all(
                        isinstance(card, CardRegion)
                        for card in detected.protection_cards
                    )
                ):
                    raise TypeError("invalid card regions")
                detection = detected
                candidate_count = detected.candidate_count
            except Exception:
                # Card enhancement is optional. Any detector failure degrades
                # to the bounded person envelope instead of blocking output.
                detection = None
        protection_cards = () if detection is None else detection.protection_cards
        try:
            protection = _build_witness_protection_mask(
                subject,
                face,
                protection_cards,
                self._options,
            )
            del subject
        except _WitnessBlurError as error:
            return finish(Suggestion.RECAPTURE, (error.reason_code,))
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.MATTING_FAILED,))

        try:
            # Only candidates that survived contact, area and final 92%
            # protection budgets may suppress a secondary printed face.
            final_reason = probe.finalize(protection.cards)
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        if final_reason is not None:
            suggestion = (
                Suggestion.REVIEW
                if final_reason is ReasonCode.INFERENCE_ERROR
                else Suggestion.RECAPTURE
            )
            return finish(suggestion, (final_reason,))

        trusted_cards = (
            ()
            if detection is None
            else tuple(card for card in detection.cards if card in protection.cards)
        )
        del detection, probe
        try:
            output, summary = _blur_witness_pixels(
                corrected, protection.matte, (), self._options
            )
        except _WitnessBlurError as error:
            return finish(Suggestion.RECAPTURE, (error.reason_code,))
        except Exception:
            return finish(Suggestion.REVIEW, (ReasonCode.INFERENCE_ERROR,))
        return finish(
            Suggestion.PASS,
            (),
            cards=trusted_cards,
            quality=quality,
            summary=summary,
            output=output,
        )


def _probe_with_components(
    image: np.ndarray,
    profile: object,
    *,
    rotator: object,
    detector: object,
    landmarker: object,
    selector: FaceCandidateSelector,
) -> _WitnessFaceProbe:
    rotation = rotator.correct(image.copy())
    corrected = getattr(rotation, "corrected_image", None)
    turns = int(getattr(rotation, "quarter_turns", -1))
    original_height, original_width = image.shape[:2]
    corrected_height, corrected_width = validate_image_input(
        corrected,
        original_height * original_width,
    )
    expected = (
        (original_height, original_width)
        if turns in (0, 2)
        else (original_width, original_height)
    )
    if turns not in range(4) or (corrected_height, corrected_width) != expected:
        raise ValueError("invalid rotation result")
    # Preserve FaceCandidateBatch.saturated. Converting to tuple here would
    # erase fail-closed overflow evidence from the bounded detector adapter.
    candidates = detector.detect(corrected)
    prepared = selector.prepare(
        corrected,
        candidates,
        landmarker,
        getattr(profile, "face_selection"),
        getattr(profile, "landmark_geometry"),
    )
    if not isinstance(prepared, FaceSelectionPreparation):
        raise TypeError("invalid face selection preparation")
    hard_reason = (
        None
        if prepared.reason_code in (None, ReasonCode.NO_FACE)
        else prepared.reason_code
    )

    def finalize(ignore_regions: tuple[CardRegion, ...]) -> ReasonCode | None:
        if prepared.reason_code is ReasonCode.NO_FACE:
            return None
        result = selector.finalize(
            corrected,
            landmarker,
            prepared,
            ignore_regions=ignore_regions,
        )
        return result.reason_code

    return _WitnessFaceProbe(
        corrected_image=corrected,
        rotation_quarter_turns=turns,
        face=prepared.box,
        reason_code=hard_reason,
        finalize=finalize,
    )


def _make_witness_face_probe(
    face_pipeline: object,
    analyze_quality: Callable[[np.ndarray, object], FaceQualityAnalysis] | None,
) -> Callable[[np.ndarray, object], _WitnessFaceProbe]:
    """Bind production face-only probe or an explicit compatibility double."""

    direct = getattr(face_pipeline, "probe", None)
    if callable(direct):
        return direct

    rotator = getattr(face_pipeline, "rotator", None)
    detector = getattr(face_pipeline, "detector", None)
    landmarker = getattr(face_pipeline, "landmarker", None)
    if (
        callable(getattr(rotator, "correct", None))
        and callable(getattr(detector, "detect", None))
        and callable(getattr(landmarker, "detect", None))
    ):
        selector = getattr(face_pipeline, "face_selector", None)
        if not isinstance(selector, FaceCandidateSelector):
            selector = FaceCandidateSelector()

        def native(image: np.ndarray, profile: object) -> _WitnessFaceProbe:
            return _probe_with_components(
                image,
                profile,
                rotator=rotator,
                detector=detector,
                landmarker=landmarker,
                selector=selector,
            )

        return native

    if not callable(analyze_quality):
        raise TypeError("face probe must provide probe or face-only components")

    def compatibility(image: np.ndarray, profile: object) -> _WitnessFaceProbe:
        analysis = analyze_quality(image, profile)
        if not isinstance(analysis, FaceQualityAnalysis):
            raise TypeError("invalid face quality analysis")
        corrected = analysis.corrected_image
        if corrected is None:
            raise TypeError("face analysis did not retain the corrected image")
        reasons = tuple(analysis.public.reason_codes)
        hard_reason = next(
            (
                reason
                for reason in reasons
                if reason
                in {
                    ReasonCode.MULTIPLE_FACES,
                    ReasonCode.LANDMARK_FAILED,
                    ReasonCode.INFERENCE_ERROR,
                    ReasonCode.INVALID_IMAGE,
                    ReasonCode.IMAGE_TOO_LARGE,
                }
            ),
            None,
        )
        return _WitnessFaceProbe(
            corrected_image=corrected,
            rotation_quarter_turns=analysis.public.rotation_quarter_turns,
            face=analysis.public.box,
            reason_code=hard_reason,
            finalize=lambda _regions: None,
        )

    return compatibility


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


def _build_owned_witness_face_probe(
    *,
    config: AlgorithmConfig,
    registry: ModelRegistry,
) -> _OwnedWitnessFaceProbe:
    """Build only the privately owned face models executed by witness blur."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be an AlgorithmConfig")
    if not isinstance(registry, ModelRegistry):
        raise TypeError("registry must be a ModelRegistry")
    isolated = registry.spawn_isolated(WITNESS_FACE_MODEL_NAMES)
    handles = {
        name: isolated.get_with_evidence(name)
        for name in WITNESS_FACE_MODEL_NAMES
    }
    sessions = {name: handle.instance for name, handle in handles.items()}
    evidence = MappingProxyType(
        {name: handle.evidence for name, handle in handles.items()}
    )
    versions = MappingProxyType(
        {name: handle.evidence.version for name, handle in handles.items()}
    )
    return _OwnedWitnessFaceProbe(
        rotator=RotationAdapter(sessions["rotation_classifier"]),
        detector=FaceBoxesAdapter(sessions["face_detector"]),
        landmarker=Landmark5Adapter(
            sessions["face_landmark_primary"],
            sessions["face_landmark_secondary"],
        ),
        selector=FaceCandidateSelector(),
        model_versions=versions,
        model_evidence=evidence,
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
    """Build a runtime owning only its face-probe and MODNet graphs."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be an AlgorithmConfig")
    if not isinstance(registry, ModelRegistry):
        raise TypeError("registry must be a ModelRegistry")
    matting_runtime = build_portrait_matting_runtime(
        config=config, registry=registry
    )
    face_pipeline = _build_owned_witness_face_probe(
        config=config, registry=registry
    )
    return _bind_witness_blur_runtime(
        face_pipeline=face_pipeline,
        matting_runtime=matting_runtime,
        card_detector=_detect_card_regions,
        config=config,
    )
