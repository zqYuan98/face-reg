"""Atomic, startup-bound orchestration for safe two-image verification."""

from __future__ import annotations

import copy
import math
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np

from face_algorithm.core.matcher import (
    MATCHER_VERSION,
    EmbeddingMatchError,
    match_embeddings,
)
from face_algorithm.core.pipeline import TrustedFaceAnalysis
from face_algorithm.runtime.calibration import (
    CalibrationState,
    TrustedCalibrationKeyStore,
    build_pipeline_fingerprint,
    fingerprint_quality_profile,
    load_image_calibration,
)
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import ModelRegistry, VerifiedModelEvidence
from face_algorithm.schemas import (
    FEATURE_DIMENSION,
    IMAGE_RECAPTURE_REASON_CODES,
    FaceAnalysisResult,
    FeatureVersion,
    ImageCalibrationSummary,
    ImageVerificationResult,
    ImageVerificationSide,
    ReasonCode,
    Suggestion,
)
from face_algorithm.verification.policy import decide_image_verification

__all__ = ["ImageVerificationRuntime", "build_image_verification_runtime"]

def _has_hard_failure(reasons: tuple[ReasonCode, ...]) -> bool:
    return any(reason not in IMAGE_RECAPTURE_REASON_CODES for reason in reasons)


def _copy_request_input(image: object) -> object:
    if isinstance(image, np.ndarray):
        return image.copy()
    try:
        return copy.copy(image)
    except Exception:
        return image


def _analysis_consistency_reason(
    analysis: TrustedFaceAnalysis,
    *,
    model_versions: Mapping[str, str],
    config_version: str,
) -> ReasonCode | None:
    public = analysis.public
    if not isinstance(public, FaceAnalysisResult):
        return ReasonCode.INFERENCE_ERROR
    if public.feature_version is not FeatureVersion.RECOGNIZER_V1_1024:
        return ReasonCode.FEATURE_VERSION_MISMATCH
    if (
        dict(public.model_versions) != dict(model_versions)
        or public.config_version != config_version
    ):
        return ReasonCode.INFERENCE_ERROR
    reasons = public.reason_codes
    if not all(isinstance(reason, ReasonCode) for reason in reasons):
        return ReasonCode.INFERENCE_ERROR
    if not reasons:
        if public.success is True and public.suggestion is Suggestion.PASS:
            return None
        return ReasonCode.INFERENCE_ERROR
    hard = _has_hard_failure(reasons)
    if hard:
        if public.success is False and public.suggestion is Suggestion.REVIEW:
            return None
        return ReasonCode.INFERENCE_ERROR
    if public.suggestion is Suggestion.RECAPTURE:
        return None
    return ReasonCode.INFERENCE_ERROR


def _embedding_contract_reason(analysis: TrustedFaceAnalysis) -> ReasonCode | None:
    if analysis.public.feature_version is not FeatureVersion.RECOGNIZER_V1_1024:
        return ReasonCode.FEATURE_VERSION_MISMATCH
    embedding = analysis.embedding
    if embedding is None:
        return ReasonCode.FEATURE_FAILED
    if (
        not isinstance(embedding, np.ndarray)
        or embedding.dtype != np.dtype(np.float32)
        or embedding.shape != (FEATURE_DIMENSION,)
    ):
        return ReasonCode.FEATURE_VERSION_MISMATCH
    if not np.all(np.isfinite(embedding)):
        return ReasonCode.FEATURE_FAILED
    norm = float(np.linalg.norm(embedding.astype(np.float64, copy=False)))
    if not math.isfinite(norm) or norm <= 0.0:
        return ReasonCode.FEATURE_FAILED
    return None


@dataclass(frozen=True, slots=True)
class _BoundImageVerifier:
    _analyze_trusted: Callable[[object, Any], TrustedFaceAnalysis]
    _reference_profile: Any
    _probe_profile: Any
    _calibration: CalibrationState
    _model_versions: Mapping[str, str]
    _config_version: str
    _matcher: Any

    def _synthetic_failure(
        self,
        reason: ReasonCode = ReasonCode.INFERENCE_ERROR,
    ) -> TrustedFaceAnalysis:
        public = FaceAnalysisResult(
            success=False,
            suggestion=Suggestion.REVIEW,
            reason_codes=(reason,),
            model_versions=self._model_versions,
            feature_version=FeatureVersion.RECOGNIZER_V1_1024,
            config_version=self._config_version,
            processing_time_ms=0.0,
            trace_id=uuid.uuid4().hex,
            rotation_quarter_turns=0,
        )
        return TrustedFaceAnalysis(
            public=public,
            embedding=None,
            corrected_image=None,
            aligned_face=None,
        )

    def _analyze(self, image: object, profile: Any) -> TrustedFaceAnalysis:
        try:
            result = self._analyze_trusted(
                _copy_request_input(image),
                profile,
            )
            if not isinstance(result, TrustedFaceAnalysis):
                raise TypeError("pipeline returned the wrong trusted result type")
            consistency_reason = _analysis_consistency_reason(
                result,
                model_versions=self._model_versions,
                config_version=self._config_version,
            )
            if consistency_reason is not None:
                return self._synthetic_failure(consistency_reason)
            return result
        except Exception:
            return self._synthetic_failure()

    @staticmethod
    def _side(
        role: str,
        analysis: TrustedFaceAnalysis | None,
        reasons: tuple[ReasonCode, ...],
    ) -> ImageVerificationSide:
        if analysis is None:
            return ImageVerificationSide(
                role=role,
                analyzed=False,
                usable=False,
                reason_codes=(),
            )
        public = analysis.public
        usable = not reasons and _embedding_contract_reason(analysis) is None
        return ImageVerificationSide(
            role=role,
            analyzed=True,
            usable=usable,
            reason_codes=reasons,
            original_width=public.original_width,
            original_height=public.original_height,
            box=public.box,
            landmarks=public.landmarks,
            quality=public.quality,
            pose=public.pose,
            rotation_quarter_turns=public.rotation_quarter_turns,
            processing_time_ms=public.processing_time_ms,
            trace_id=public.trace_id,
        )

    def _result(
        self,
        *,
        started: float,
        reference: TrustedFaceAnalysis,
        probe: TrustedFaceAnalysis | None,
        reference_reasons: tuple[ReasonCode, ...],
        probe_reasons: tuple[ReasonCode, ...],
        score: float | None,
    ) -> ImageVerificationResult:
        decision = decide_image_verification(
            reference_reason_codes=reference_reasons,
            probe_reason_codes=probe_reasons,
            score=score,
            calibration=self._calibration,
        )
        calibration = ImageCalibrationSummary(
            status=self._calibration.status.value,
            calibration_id=self._calibration.calibration_id,
            artifact_sha256=self._calibration.artifact_sha256,
        )
        return ImageVerificationResult(
            success=decision.success,
            suggestion=decision.suggestion,
            reason_codes=decision.reason_codes,
            model_versions=self._model_versions,
            feature_version=FeatureVersion.RECOGNIZER_V1_1024,
            config_version=self._config_version,
            processing_time_ms=max(0.0, (time.perf_counter() - started) * 1000.0),
            trace_id=uuid.uuid4().hex,
            reference=self._side("reference", reference, reference_reasons),
            probe=self._side("probe", probe, probe_reasons),
            similarity_score=score,
            matcher_version=MATCHER_VERSION,
            feature_dimension=FEATURE_DIMENSION,
            decision_threshold=decision.decision_threshold,
            calibration=calibration,
        )

    def verify(
        self,
        reference_image: object,
        probe_image: object,
    ) -> ImageVerificationResult:
        started = time.perf_counter()
        reference = self._analyze(reference_image, self._reference_profile)
        reference_reasons = reference.public.reason_codes

        if _has_hard_failure(reference_reasons):
            return self._result(
                started=started,
                reference=reference,
                probe=None,
                reference_reasons=reference_reasons,
                probe_reasons=(),
                score=None,
            )
        if not reference_reasons:
            reference_contract_reason = _embedding_contract_reason(reference)
            if reference_contract_reason is not None:
                return self._result(
                    started=started,
                    reference=reference,
                    probe=None,
                    reference_reasons=(reference_contract_reason,),
                    probe_reasons=(),
                    score=None,
                )

        probe = self._analyze(probe_image, self._probe_profile)
        probe_reasons = probe.public.reason_codes
        score: float | None = None
        if not probe_reasons:
            probe_contract_reason = _embedding_contract_reason(probe)
            if probe_contract_reason is not None:
                probe_reasons = (probe_contract_reason,)
        if not reference_reasons and not probe_reasons:
            try:
                assert reference.embedding is not None and probe.embedding is not None
                match = self._matcher(
                    reference.embedding,
                    probe.embedding,
                    reference_version=reference.public.feature_version,
                    probe_version=probe.public.feature_version,
                )
                score = match.score
            except EmbeddingMatchError as error:
                probe_reasons = (error.reason_code,)
            except Exception:
                probe_reasons = (ReasonCode.INFERENCE_ERROR,)

        return self._result(
            started=started,
            reference=reference,
            probe=probe,
            reference_reasons=reference_reasons,
            probe_reasons=probe_reasons,
            score=score,
        )


@dataclass(frozen=True, slots=True, init=False)
class ImageVerificationRuntime:
    """Immutable request-time bundle produced only by the startup builder."""

    reference_profile: Any
    probe_profile: Any
    model_versions: Mapping[str, str]
    config_version: str
    reference_profile_fingerprint: str
    probe_profile_fingerprint: str
    pipeline_fingerprint: str
    calibration: CalibrationState
    _model_evidence_snapshot: Mapping[str, VerifiedModelEvidence] = field(
        repr=False,
    )
    _verifier: _BoundImageVerifier = field(repr=False)

    def __init__(self) -> None:
        raise TypeError("use build_image_verification_runtime")

    @classmethod
    def _from_builder(
        cls,
        *,
        reference_profile: Any,
        probe_profile: Any,
        model_versions: Mapping[str, str],
        config_version: str,
        reference_profile_fingerprint: str,
        probe_profile_fingerprint: str,
        pipeline_fingerprint: str,
        calibration: CalibrationState,
        model_evidence_snapshot: Mapping[str, VerifiedModelEvidence],
        verifier: _BoundImageVerifier,
    ) -> ImageVerificationRuntime:
        instance = object.__new__(cls)
        values = {
            "reference_profile": reference_profile,
            "probe_profile": probe_profile,
            "model_versions": MappingProxyType(dict(model_versions)),
            "config_version": config_version,
            "reference_profile_fingerprint": reference_profile_fingerprint,
            "probe_profile_fingerprint": probe_profile_fingerprint,
            "pipeline_fingerprint": pipeline_fingerprint,
            "calibration": calibration,
            "_model_evidence_snapshot": MappingProxyType(
                dict(model_evidence_snapshot)
            ),
            "_verifier": verifier,
        }
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        return instance

    def verify(
        self,
        reference_image: object,
        probe_image: object,
    ) -> ImageVerificationResult:
        return self._verifier.verify(reference_image, probe_image)


def _bind_image_verification_runtime(
    *,
    pipeline: Any,
    config: AlgorithmConfig,
    key_store: TrustedCalibrationKeyStore | None,
) -> ImageVerificationRuntime:
    """Bind an internally owned pipeline; exposed only for isolated unit tests."""

    reference_profile = getattr(
        config.quality_profiles,
        config.image_verification.reference_quality_profile,
    )
    probe_profile = getattr(
        config.quality_profiles,
        config.image_verification.probe_quality_profile,
    )
    reference_fingerprint = fingerprint_quality_profile(
        reference_profile,
        role="reference",
    )
    probe_fingerprint = fingerprint_quality_profile(probe_profile, role="probe")
    raw_evidence = getattr(pipeline, "model_evidence", None)
    if not isinstance(raw_evidence, Mapping):
        raise TypeError("pipeline must contain an immutable verified evidence snapshot")
    evidence_snapshot = dict(raw_evidence)
    raw_model_versions = getattr(pipeline, "model_versions", None)
    if not isinstance(raw_model_versions, Mapping):
        raise TypeError("pipeline must contain model version metadata")
    model_versions = dict(raw_model_versions)
    expected_versions = {
        role: evidence.version for role, evidence in evidence_snapshot.items()
    }
    if model_versions != expected_versions:
        raise ValueError("pipeline model versions do not match verified evidence")
    config_version = getattr(pipeline, "config_version", None)
    if config_version != config.config_version:
        raise ValueError("pipeline config version does not match runtime config")
    model_versions_snapshot: Mapping[str, str] = MappingProxyType(model_versions)
    pipeline_fingerprint = build_pipeline_fingerprint(
        evidence_snapshot,
        reference_profile_fingerprint=reference_fingerprint,
        probe_profile_fingerprint=probe_fingerprint,
    )
    calibration = load_image_calibration(
        config.image_verification,
        key_store=key_store,
        model_evidence=evidence_snapshot,
        quality_profiles=config.quality_profiles,
    )
    analyze_trusted = getattr(pipeline, "analyze_trusted", None)
    if not callable(analyze_trusted):
        raise TypeError("pipeline must provide a trusted analysis callable")
    verifier = _BoundImageVerifier(
        _analyze_trusted=analyze_trusted,
        _reference_profile=reference_profile,
        _probe_profile=probe_profile,
        _calibration=calibration,
        _model_versions=model_versions_snapshot,
        _config_version=config_version,
        _matcher=match_embeddings,
    )
    return ImageVerificationRuntime._from_builder(
        reference_profile=reference_profile,
        probe_profile=probe_profile,
        model_versions=model_versions_snapshot,
        config_version=config_version,
        reference_profile_fingerprint=reference_fingerprint,
        probe_profile_fingerprint=probe_fingerprint,
        pipeline_fingerprint=pipeline_fingerprint,
        calibration=calibration,
        model_evidence_snapshot=evidence_snapshot,
        verifier=verifier,
    )


def build_image_verification_runtime(
    *,
    registry: ModelRegistry,
    config: AlgorithmConfig,
    key_store: TrustedCalibrationKeyStore | None,
) -> ImageVerificationRuntime:
    """Construct and own the complete verification execution graph at startup.

    The public builder deliberately accepts no pre-built pipeline.  This keeps
    caller-retained mutable adapters out of the calibrated request-time graph.
    """

    from face_algorithm.core.adapters import build_owned_face_pipeline

    pipeline = build_owned_face_pipeline(config=config, registry=registry)
    return _bind_image_verification_runtime(
        pipeline=pipeline,
        config=config,
        key_store=key_store,
    )
