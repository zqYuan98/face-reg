from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from face_algorithm.core.pipeline import TrustedFaceAnalysis
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import (
    FaceAnalysisResult,
    FeatureVersion,
    ImageVerificationResult,
    ImageVerificationSide,
    ReasonCode,
    Suggestion,
)
from face_algorithm.verification.image_verifier import (
    _bind_image_verification_runtime,
    build_image_verification_runtime,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "algorithm.yaml"


def _embedding(first: float = 1.0, second: float = 0.0) -> np.ndarray:
    value = np.zeros(1024, dtype=np.float32)
    value[0] = np.float32(first)
    value[1] = np.float32(second)
    value /= np.linalg.norm(value)
    return value


def _trusted(
    *,
    reasons: tuple[ReasonCode, ...] = (),
    embedding: np.ndarray | None = None,
    suggestion: Suggestion | None = None,
) -> TrustedFaceAnalysis:
    if suggestion is None:
        suggestion = Suggestion.PASS if not reasons else Suggestion.RECAPTURE
    public = FaceAnalysisResult(
        success=suggestion is not Suggestion.REVIEW,
        suggestion=suggestion,
        reason_codes=reasons,
        model_versions={"face_recognizer": "test-v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="test-config-v1",
        processing_time_ms=1.0,
        trace_id="side-trace",
        original_width=400,
        original_height=400,
        rotation_quarter_turns=0,
    )
    return TrustedFaceAnalysis(
        public=public,
        embedding=embedding,
        corrected_image=None,
        aligned_face=None,
    )


class QueuePipeline:
    def __init__(
        self,
        outputs,
        *,
        model_evidence=None,
        config_version=None,
        normalize_metadata: bool = True,
    ) -> None:
        self.model_evidence = model_evidence or {}
        self.model_versions = (
            {role: evidence.version for role, evidence in self.model_evidence.items()}
            if self.model_evidence
            else {"face_recognizer": "test-v1"}
        )
        self.config_version = config_version or "test-config-v1"
        self.outputs = [
            self._with_runtime_metadata(output) if normalize_metadata else output
            for output in outputs
        ]
        self.calls: list[tuple[np.ndarray, object]] = []

    def _with_runtime_metadata(self, output):
        if not isinstance(output, TrustedFaceAnalysis):
            return output
        return TrustedFaceAnalysis(
            public=output.public.model_copy(
                update={
                    "model_versions": self.model_versions,
                    "config_version": self.config_version,
                }
            ),
            embedding=output.embedding,
            corrected_image=output.corrected_image,
            aligned_face=output.aligned_face,
        )

    def analyze_trusted(self, image: np.ndarray, profile: object) -> TrustedFaceAnalysis:
        self.calls.append((image, profile))
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output


def _runtime(
    outputs,
    *,
    config,
    key_store,
    model_evidence,
):
    pipeline = QueuePipeline(
        outputs,
        model_evidence=model_evidence,
        config_version=config.config_version,
    )
    runtime = _bind_image_verification_runtime(
        pipeline=pipeline,
        config=config,
        key_store=key_store,
    )
    return runtime, pipeline


def test_public_builder_constructs_and_owns_the_face_pipeline() -> None:
    assert tuple(signature(build_image_verification_runtime).parameters) == (
        "registry",
        "config",
        "key_store",
    )


def _stable_pipeline_for_public_builder(active_calibration_bundle):
    evidence = active_calibration_bundle.evidence
    versions = {role: item.version for role, item in evidence.items()}
    config_version = active_calibration_bundle.config.config_version

    class StablePipeline:
        def __init__(self) -> None:
            self.model_evidence = evidence
            self.model_versions = versions
            self.config_version = config_version

        def analyze_trusted(self, _image, _profile):
            result = _trusted(embedding=_embedding())
            return TrustedFaceAnalysis(
                public=result.public.model_copy(
                    update={
                        "model_versions": versions,
                        "config_version": config_version,
                    }
                ),
                embedding=result.embedding,
                corrected_image=None,
                aligned_face=None,
            )

    return StablePipeline()


def test_public_builder_rejects_external_pipeline_and_ignores_its_nested_mutation(
    active_calibration_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_algorithm.core import adapters

    external_session = SimpleNamespace(vector=_embedding(0.0, 1.0))
    external_pipeline = SimpleNamespace(
        embedder=SimpleNamespace(session=external_session),
        analyze_trusted=lambda *_args: _trusted(
            embedding=external_session.vector.copy()
        ),
    )
    registry = object()
    calls = []

    def construct_owned_pipeline(*, config, registry):
        calls.append((config, registry))
        return _stable_pipeline_for_public_builder(active_calibration_bundle)

    monkeypatch.setattr(
        adapters,
        "build_owned_face_pipeline",
        construct_owned_pipeline,
    )
    with pytest.raises(TypeError, match="unexpected keyword argument 'pipeline'"):
        build_image_verification_runtime(
            pipeline=external_pipeline,
            registry=registry,
            config=active_calibration_bundle.config,
            key_store=active_calibration_bundle.key_store,
        )
    runtime = build_image_verification_runtime(
        registry=registry,
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
    )
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    before = runtime.verify(image, image)

    external_session.vector[:] = _embedding()
    external_pipeline.embedder.session = object()
    external_pipeline.analyze_trusted = lambda *_args: _trusted(
        embedding=_embedding(0.0, 1.0)
    )
    after = runtime.verify(image, image)

    assert calls == [(active_calibration_bundle.config, registry)]
    assert before.similarity_score == after.similarity_score == pytest.approx(1.0)
    assert before.suggestion is after.suggestion is Suggestion.PASS
    assert not hasattr(runtime, "pipeline")


def test_uncalibrated_identical_images_have_finite_score_but_never_pass(
    active_calibration_bundle,
) -> None:
    vector = _embedding()
    config = load_config(DEFAULT_CONFIG)
    runtime, pipeline = _runtime(
        [_trusted(embedding=vector), _trusted(embedding=vector.copy())],
        config=config,
        key_store=None,
        model_evidence=active_calibration_bundle.evidence,
    )
    reference_image = np.zeros((4, 4, 3), dtype=np.uint8)
    probe_image = np.ones((4, 4, 3), dtype=np.uint8)

    result = runtime.verify(reference_image, probe_image)

    assert isinstance(result, ImageVerificationResult)
    assert result.success is True
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.THRESHOLD_UNCALIBRATED,)
    assert result.similarity_score == pytest.approx(1.0)
    assert result.decision_threshold is None
    assert result.matcher_version == "l2_dot_v1"
    assert result.feature_dimension == 1024
    assert result.calibration.status == "UNCALIBRATED"
    assert result.model_versions == {
        role: evidence.version
        for role, evidence in active_calibration_bundle.evidence.items()
    }
    assert result.config_version == config.config_version
    assert result.trace_id
    assert math.isfinite(result.processing_time_ms)
    assert result.processing_time_ms >= 0.0
    assert not hasattr(result.reference, "suggestion")
    assert not hasattr(result.probe, "suggestion")
    assert len(pipeline.calls) == 2
    np.testing.assert_array_equal(pipeline.calls[0][0], reference_image)
    np.testing.assert_array_equal(pipeline.calls[1][0], probe_image)
    assert pipeline.calls[0][1] is config.quality_profiles.entry_verify
    assert pipeline.calls[1][1] is config.quality_profiles.entry_verify


@pytest.mark.parametrize(
    ("probe", "suggestion", "reasons"),
    [
        (_embedding(0.8, 0.6), Suggestion.PASS, ()),
        (
            _embedding(0.5, 0.8660254),
            Suggestion.REVIEW,
            (ReasonCode.IDENTITY_UNCERTAIN,),
        ),
    ],
)
def test_signed_active_calibration_is_the_only_path_to_pass(
    probe: np.ndarray,
    suggestion: Suggestion,
    reasons: tuple[ReasonCode, ...],
    active_calibration_bundle,
) -> None:
    runtime, _ = _runtime(
        [_trusted(embedding=_embedding()), _trusted(embedding=probe)],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.suggestion is suggestion
    assert result.reason_codes == reasons
    assert result.decision_threshold == pytest.approx(0.75)
    assert result.calibration.status == "ACTIVE"
    assert result.calibration.calibration_id == "signed-test-calibration-v1"


def test_quality_failure_requests_recapture_without_computing_a_score(
    active_calibration_bundle,
) -> None:
    runtime, _ = _runtime(
        [
            _trusted(
                reasons=(ReasonCode.TOO_BLURRY,),
                embedding=_embedding(),
            ),
            _trusted(embedding=_embedding()),
        ],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.success is True
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.TOO_BLURRY,)
    assert result.similarity_score is None


def test_system_failure_beats_other_side_recapture_and_is_unsuccessful(
    active_calibration_bundle,
) -> None:
    runtime, _ = _runtime(
        [
            _trusted(reasons=(ReasonCode.NO_FACE,)),
            _trusted(
                reasons=(ReasonCode.INFERENCE_ERROR,),
                suggestion=Suggestion.REVIEW,
            ),
        ],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.NO_FACE, ReasonCode.INFERENCE_ERROR)
    assert result.similarity_score is None


def test_matcher_contract_failure_is_review_and_never_exposes_embedding(
    active_calibration_bundle,
) -> None:
    bad_dtype = _embedding().astype(np.float64)
    runtime, _ = _runtime(
        [_trusted(embedding=_embedding()), _trusted(embedding=bad_dtype)],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.FEATURE_VERSION_MISMATCH,)
    assert result.similarity_score is None
    payload = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    assert "embedding" not in payload.casefold()
    assert "feature_vector" not in payload.casefold()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        type(result.reference)(
            **result.reference.model_dump(),
            embedding=[0.0] * 1024,
        )


def test_available_embeddings_invoke_matcher_exactly_once(
    active_calibration_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_algorithm.verification import image_verifier as verifier_module

    calls = []
    real_matcher = verifier_module.match_embeddings

    def counting_matcher(*args, **kwargs):
        calls.append((args, kwargs))
        return real_matcher(*args, **kwargs)

    monkeypatch.setattr(verifier_module, "match_embeddings", counting_matcher)
    runtime, _ = _runtime(
        [_trusted(embedding=_embedding()), _trusted(embedding=_embedding())],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.suggestion is Suggestion.PASS
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["missing", "version"])
def test_missing_embedding_or_side_version_mismatch_skips_matcher(
    failure: str,
    active_calibration_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_algorithm.verification import image_verifier as verifier_module

    reference = _trusted(embedding=_embedding())
    if failure == "missing":
        probe = _trusted(embedding=None)
        expected = ReasonCode.FEATURE_FAILED
    else:
        probe = _trusted(embedding=_embedding())
        probe = TrustedFaceAnalysis(
            public=probe.public.model_copy(
                update={"feature_version": "recognizer_v0_1024"}
            ),
            embedding=probe.embedding,
            corrected_image=None,
            aligned_face=None,
        )
        expected = ReasonCode.FEATURE_VERSION_MISMATCH

    def forbidden_matcher(*_args, **_kwargs):
        raise AssertionError("matcher must not be called")

    monkeypatch.setattr(verifier_module, "match_embeddings", forbidden_matcher)
    runtime, _ = _runtime(
        [reference, probe],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (expected,)
    assert result.similarity_score is None


@pytest.mark.parametrize("failure", ["missing", "version"])
def test_probe_contract_failure_overrides_reference_recapture_without_matching(
    failure: str,
    active_calibration_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_algorithm.verification import image_verifier as verifier_module

    probe = _trusted(embedding=None if failure == "missing" else _embedding())
    expected = ReasonCode.FEATURE_FAILED
    if failure == "version":
        probe = TrustedFaceAnalysis(
            public=probe.public.model_copy(
                update={"feature_version": "recognizer_v0_1024"}
            ),
            embedding=probe.embedding,
            corrected_image=None,
            aligned_face=None,
        )
        expected = ReasonCode.FEATURE_VERSION_MISMATCH

    def forbidden_matcher(*_args, **_kwargs):
        raise AssertionError("matcher must not be called")

    monkeypatch.setattr(verifier_module, "match_embeddings", forbidden_matcher)
    runtime, _ = _runtime(
        [_trusted(reasons=(ReasonCode.TOO_BLURRY,)), probe],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.TOO_BLURRY, expected)
    assert result.similarity_score is None


def test_verifier_passes_copies_and_preserves_both_input_arrays(
    active_calibration_bundle,
) -> None:
    vector = _embedding()
    pipeline = QueuePipeline(
        [_trusted(embedding=vector), _trusted(embedding=vector.copy())],
        model_evidence=active_calibration_bundle.evidence,
        config_version=active_calibration_bundle.config.config_version,
    )
    original_analyze = pipeline.analyze_trusted

    def mutating_analyze(image: np.ndarray, profile: object) -> TrustedFaceAnalysis:
        result = original_analyze(image, profile)
        image.fill(255)
        return result

    pipeline.analyze_trusted = mutating_analyze
    runtime = _bind_image_verification_runtime(
        pipeline=pipeline,
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
    )
    reference = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
    probe = np.flip(reference, axis=0).copy()
    reference_before = reference.copy()
    probe_before = probe.copy()

    runtime.verify(reference, probe)

    np.testing.assert_array_equal(reference, reference_before)
    np.testing.assert_array_equal(probe, probe_before)


def test_profiles_are_server_fixed_and_not_request_parameters(
    active_calibration_bundle,
) -> None:
    runtime, pipeline = _runtime(
        [_trusted(embedding=_embedding()), _trusted(embedding=_embedding())],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )
    image = np.zeros((4, 4, 3), dtype=np.uint8)

    runtime.verify(image, image)

    assert tuple(signature(runtime.verify).parameters) == (
        "reference_image",
        "probe_image",
    )
    assert pipeline.calls[0][1] is runtime.reference_profile
    assert pipeline.calls[1][1] is runtime.probe_profile
    with pytest.raises(TypeError):
        runtime.verify(image, image, reference_profile=object())


def test_runtime_captures_execution_and_audit_bindings_without_exposing_pipeline(
    active_calibration_bundle,
) -> None:
    runtime, pipeline = _runtime(
        [
            _trusted(embedding=_embedding()),
            _trusted(embedding=_embedding(0.5, 0.8660254)),
        ],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )
    expected_versions = dict(pipeline.model_versions)
    expected_config_version = pipeline.config_version
    pipeline.model_versions = {"face_recognizer": "tampered"}
    pipeline.config_version = "tampered"
    pipeline.embedder = object()
    pipeline.analyze_trusted = lambda *_args: _trusted(embedding=_embedding())

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert dict(result.model_versions) == expected_versions
    assert result.config_version == expected_config_version
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.IDENTITY_UNCERTAIN,)
    assert not hasattr(runtime, "pipeline")


@pytest.mark.parametrize(
    "binding",
    [
        "_analyze_trusted",
        "_matcher",
        "_calibration",
        "_model_versions",
        "_config_version",
        "_reference_profile",
        "_probe_profile",
    ],
)
def test_bound_verifier_fields_cannot_be_replaced_after_builder(
    binding: str,
    active_calibration_bundle,
) -> None:
    runtime, _ = _runtime(
        [_trusted(embedding=_embedding()), _trusted(embedding=_embedding())],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    with pytest.raises(AttributeError):
        setattr(runtime._verifier, binding, object())


@pytest.mark.parametrize(
    ("drift", "expected_reason"),
    [
        ("model_versions", ReasonCode.INFERENCE_ERROR),
        ("config_version", ReasonCode.INFERENCE_ERROR),
        ("feature_version", ReasonCode.FEATURE_VERSION_MISMATCH),
    ],
)
def test_reference_side_audit_metadata_drift_fails_closed_and_stops_probe(
    drift: str,
    expected_reason: ReasonCode,
    active_calibration_bundle,
) -> None:
    expected_versions = {
        role: evidence.version
        for role, evidence in active_calibration_bundle.evidence.items()
    }
    analysis = _trusted(embedding=_embedding())
    updates = {
        "model_versions": expected_versions,
        "config_version": active_calibration_bundle.config.config_version,
        "feature_version": FeatureVersion.RECOGNIZER_V1_1024,
    }
    if drift == "model_versions":
        updates[drift] = {**expected_versions, "face_recognizer": "tampered"}
    elif drift == "config_version":
        updates[drift] = "tampered"
    else:
        updates[drift] = "recognizer_v0_1024"
    drifted = TrustedFaceAnalysis(
        public=analysis.public.model_copy(update=updates),
        embedding=analysis.embedding,
        corrected_image=None,
        aligned_face=None,
    )
    pipeline = QueuePipeline(
        [drifted, AssertionError("probe must not run")],
        model_evidence=active_calibration_bundle.evidence,
        config_version=active_calibration_bundle.config.config_version,
        normalize_metadata=False,
    )
    runtime = _bind_image_verification_runtime(
        pipeline=pipeline,
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert len(pipeline.calls) == 1
    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (expected_reason,)
    assert result.probe.analyzed is False


def test_recapture_side_needs_protocol_metadata_but_not_an_embedding(
    active_calibration_bundle,
) -> None:
    runtime, pipeline = _runtime(
        [
            _trusted(reasons=(ReasonCode.TOO_BLURRY,), embedding=None),
            _trusted(embedding=_embedding()),
        ],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert len(pipeline.calls) == 2
    assert result.success is True
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.TOO_BLURRY,)


@pytest.mark.parametrize("side", ["reference", "probe"])
def test_recapture_side_with_wrong_feature_protocol_is_hard_failure(
    side: str,
    active_calibration_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_algorithm.verification import image_verifier as verifier_module

    expected_versions = {
        role: evidence.version
        for role, evidence in active_calibration_bundle.evidence.items()
    }
    analysis = _trusted(reasons=(ReasonCode.TOO_BLURRY,), embedding=None)
    drifted = TrustedFaceAnalysis(
        public=analysis.public.model_copy(
            update={
                "model_versions": expected_versions,
                "config_version": active_calibration_bundle.config.config_version,
                "feature_version": "recognizer_v0_1024",
            }
        ),
        embedding=None,
        corrected_image=None,
        aligned_face=None,
    )
    outputs = (
        [drifted, AssertionError("probe must not run")]
        if side == "reference"
        else [_trusted(embedding=_embedding()), drifted]
    )
    matcher_calls: list[object] = []

    def forbidden_matcher(*_args, **_kwargs):
        matcher_calls.append(object())
        raise AssertionError("matcher must not be called")

    monkeypatch.setattr(verifier_module, "match_embeddings", forbidden_matcher)
    pipeline = QueuePipeline(
        outputs,
        model_evidence=active_calibration_bundle.evidence,
        config_version=active_calibration_bundle.config.config_version,
    )
    runtime = _bind_image_verification_runtime(
        pipeline=pipeline,
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert len(pipeline.calls) == (1 if side == "reference" else 2)
    assert matcher_calls == []
    assert result.success is False
    assert result.reason_codes == (ReasonCode.FEATURE_VERSION_MISMATCH,)


def test_builder_rejects_model_version_metadata_that_disagrees_with_evidence(
    active_calibration_bundle,
) -> None:
    pipeline = QueuePipeline(
        [_trusted(embedding=_embedding()), _trusted(embedding=_embedding())],
        model_evidence=active_calibration_bundle.evidence,
        config_version=active_calibration_bundle.config.config_version,
    )
    pipeline.model_versions["face_recognizer"] = "tampered"

    with pytest.raises(ValueError, match="model version"):
        _bind_image_verification_runtime(
            pipeline=pipeline,
            config=active_calibration_bundle.config,
            key_store=active_calibration_bundle.key_store,
        )


def test_public_json_contains_only_safe_summaries_and_no_biometric_buffers(
    active_calibration_bundle,
) -> None:
    runtime, _ = _runtime(
        [_trusted(embedding=_embedding()), _trusted(embedding=_embedding())],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )
    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )
    payload = result.model_dump(mode="json")

    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert key.casefold() not in {
                    "embedding",
                    "feature_vector",
                    "aligned_face",
                    "corrected_image",
                    "artifact",
                }
                walk(nested)
        elif isinstance(value, list):
            assert len(value) != 1024
            for nested in value:
                walk(nested)

    walk(payload)
    serialized = json.dumps(payload, sort_keys=True)
    assert "embedding" not in serialized.casefold()
    assert "feature_vector" not in serialized.casefold()


def test_unexpected_reference_pipeline_exception_stops_probe_inference(
    active_calibration_bundle,
) -> None:
    runtime, pipeline = _runtime(
        [RuntimeError("boom"), _trusted(embedding=_embedding())],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert len(pipeline.calls) == 1
    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert result.reference.analyzed is True
    assert result.probe.analyzed is False


@pytest.mark.parametrize(
    "reason",
    [ReasonCode.INFERENCE_ERROR, ReasonCode.FEATURE_FAILED],
)
def test_returned_reference_hard_failure_also_stops_probe_inference(
    reason: ReasonCode,
    active_calibration_bundle,
) -> None:
    runtime, pipeline = _runtime(
        [
            _trusted(reasons=(reason,), suggestion=Suggestion.REVIEW),
            AssertionError("probe must not run"),
        ],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert len(pipeline.calls) == 1
    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (reason,)
    assert result.probe.analyzed is False


def test_wrong_pipeline_return_type_fails_closed_and_stops_probe(
    active_calibration_bundle,
) -> None:
    runtime, pipeline = _runtime(
        [object(), AssertionError("probe must not run")],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert len(pipeline.calls) == 1
    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "role": "probe",
            "analyzed": False,
            "usable": True,
            "reason_codes": (),
        },
        {
            "role": "reference",
            "analyzed": True,
            "usable": True,
            "reason_codes": (ReasonCode.NO_FACE,),
            "processing_time_ms": 1.0,
            "trace_id": "trace",
        },
        {
            "role": "reference",
            "analyzed": True,
            "usable": False,
            "reason_codes": (),
            "rotation_quarter_turns": 0,
            "processing_time_ms": 1.0,
            "trace_id": "trace",
        },
        {
            "role": "reference",
            "analyzed": False,
            "usable": False,
            "reason_codes": (),
            "processing_time_ms": 1.0,
            "trace_id": "trace",
        },
    ],
)
def test_public_side_schema_rejects_self_contradictory_state(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        ImageVerificationSide(**kwargs)


@pytest.mark.parametrize(
    "mutation",
    [
        "review_without_reason",
        "uncalibrated_reason_on_active",
        "identity_score_above_threshold",
        "hard_failure_with_score",
    ],
)
def test_public_result_schema_rejects_invalid_review_combinations(
    mutation: str,
    active_calibration_bundle,
) -> None:
    runtime, _ = _runtime(
        [_trusted(embedding=_embedding()), _trusted(embedding=_embedding())],
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )
    payload = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    ).model_dump()
    payload["suggestion"] = Suggestion.REVIEW
    payload["success"] = True
    if mutation == "review_without_reason":
        payload["reason_codes"] = ()
    elif mutation == "uncalibrated_reason_on_active":
        payload["reason_codes"] = (ReasonCode.THRESHOLD_UNCALIBRATED,)
    elif mutation == "identity_score_above_threshold":
        payload["reason_codes"] = (ReasonCode.IDENTITY_UNCERTAIN,)
    else:
        payload["success"] = False
        payload["reason_codes"] = (ReasonCode.INFERENCE_ERROR,)

    with pytest.raises(ValidationError):
        ImageVerificationResult(**payload)


@pytest.mark.parametrize("outcome", ["recapture", "hard_review"])
def test_public_result_top_reasons_must_equal_stable_side_reason_merge(
    outcome: str,
    active_calibration_bundle,
) -> None:
    if outcome == "recapture":
        outputs = [
            _trusted(reasons=(ReasonCode.TOO_BLURRY,)),
            _trusted(reasons=(ReasonCode.BAD_POSE,)),
        ]
        forged_reasons = (ReasonCode.BAD_POSE, ReasonCode.TOO_BLURRY)
    else:
        outputs = [
            _trusted(reasons=(ReasonCode.NO_FACE,)),
            _trusted(
                reasons=(ReasonCode.INFERENCE_ERROR,),
                suggestion=Suggestion.REVIEW,
            ),
        ]
        forged_reasons = (ReasonCode.INFERENCE_ERROR,)
    runtime, _ = _runtime(
        outputs,
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
        model_evidence=active_calibration_bundle.evidence,
    )
    payload = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    ).model_dump()
    payload["reason_codes"] = forged_reasons

    with pytest.raises(ValidationError):
        ImageVerificationResult(**payload)


@pytest.mark.parametrize(
    ("side", "invalid_input"),
    [
        ("reference", None),
        ("reference", []),
        ("reference", np.empty((0, 0, 3), dtype=np.uint8)),
        ("reference", np.zeros((4, 4, 3), dtype=np.float32)),
        ("probe", None),
        ("probe", []),
        ("probe", np.empty((0, 0, 3), dtype=np.uint8)),
        ("probe", np.zeros((4, 4, 3), dtype=np.float32)),
    ],
    ids=[
        "reference-none",
        "reference-list",
        "reference-empty",
        "reference-dtype",
        "probe-none",
        "probe-list",
        "probe-empty",
        "probe-dtype",
    ],
)
def test_invalid_inputs_return_structured_recapture_without_matching(
    side: str,
    invalid_input,
    active_calibration_bundle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from face_algorithm.verification import image_verifier as verifier_module

    class InputValidatingPipeline(QueuePipeline):
        def analyze_trusted(self, image, profile):
            self.calls.append((image, profile))
            valid = bool(
                isinstance(image, np.ndarray)
                and image.dtype == np.uint8
                and image.ndim == 3
                and image.shape[0] > 0
                and image.shape[1] > 0
            )
            if valid:
                return self._with_runtime_metadata(
                    _trusted(embedding=_embedding())
                )
            return self._with_runtime_metadata(
                _trusted(reasons=(ReasonCode.INVALID_IMAGE,))
            )

    def forbidden_matcher(*_args, **_kwargs):
        raise AssertionError("matcher must not be called")

    monkeypatch.setattr(verifier_module, "match_embeddings", forbidden_matcher)
    pipeline = InputValidatingPipeline(
        [],
        model_evidence=active_calibration_bundle.evidence,
        config_version=active_calibration_bundle.config.config_version,
    )
    runtime = _bind_image_verification_runtime(
        pipeline=pipeline,
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
    )
    valid_image = np.zeros((4, 4, 3), dtype=np.uint8)
    reference = invalid_input if side == "reference" else valid_image
    probe = invalid_input if side == "probe" else valid_image

    result = runtime.verify(reference, probe)

    assert result.success is True
    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (ReasonCode.INVALID_IMAGE,)
    assert result.similarity_score is None


def test_atomic_runtime_does_not_reopen_artifacts_during_requests(
    active_calibration_bundle,
) -> None:
    vector = _embedding()
    pipeline = QueuePipeline(
        [_trusted(embedding=vector), _trusted(embedding=vector.copy())],
        model_evidence=active_calibration_bundle.evidence,
        config_version=active_calibration_bundle.config.config_version,
    )
    runtime = _bind_image_verification_runtime(
        pipeline=pipeline,
        config=active_calibration_bundle.config,
        key_store=active_calibration_bundle.key_store,
    )
    artifact_path = active_calibration_bundle.config.image_verification.artifact_path
    signature_path = active_calibration_bundle.config.image_verification.signature_path
    artifact_path.unlink()
    signature_path.unlink()

    result = runtime.verify(
        np.zeros((4, 4, 3), dtype=np.uint8),
        np.zeros((4, 4, 3), dtype=np.uint8),
    )

    assert result.suggestion is Suggestion.PASS
    assert runtime.calibration is not None
    assert runtime.pipeline_fingerprint
    assert runtime.reference_profile_fingerprint
    assert runtime.probe_profile_fingerprint
    with pytest.raises(FrozenInstanceError):
        runtime.pipeline_fingerprint = "0" * 64
