"""Image trial decisions must survive complete strict HTTP serialization."""

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.services import DefaultAlgorithmServices
from face_algorithm.core.pipeline import TrustedFaceAnalysis
from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.runtime.model_registry import VerifiedModelEvidence
from face_algorithm.schemas import (
    FaceAnalysisResult, FeatureVersion, HealthLevel, HealthStatus,
    ImageCalibrationSummary, ImageVerificationResult, ReasonCode, Suggestion,
)

from .conftest import FakeServices, encode_jpeg, image_verification_result


def _trial_result(score=0.55):
    payload = image_verification_result().model_dump(mode="python")
    payload.update(
        calibration=ImageCalibrationSummary(status="CONFIGURED_UNVALIDATED"),
        decision_threshold=0.55,
        similarity_score=score,
        reason_codes=(ReasonCode(
            "IMAGE_TRIAL_THRESHOLD_MATCHED" if score >= 0.55 else "IMAGE_TRIAL_THRESHOLD_NOT_MATCHED"
        ),),
    )
    return ImageVerificationResult.model_validate(payload)


def _post_images(client, **kwargs):
    return client.post("/api/v2/verify/images", files={
        name: (f"{name}.jpg", encode_jpeg(), "image/jpeg")
        for name in ("reference_image", "probe_image")
    }, **kwargs)


@pytest.mark.parametrize("score, code", [
    (0.5499999, "IMAGE_TRIAL_THRESHOLD_NOT_MATCHED"),
    (0.55, "IMAGE_TRIAL_THRESHOLD_MATCHED"),
    (0.5500001, "IMAGE_TRIAL_THRESHOLD_MATCHED"),
])
def test_http_image_trial_retains_fields_and_nonpassing_decision(algorithm_config, score, code):
    result = _trial_result(score)

    class Services(FakeServices):
        def verify_images(self, *args, **kwargs):
            return result

    with TestClient(create_app(config=algorithm_config, services=Services())) as client:
        # Existing upload-only contract cannot override a bound threshold.
        response = _post_images(client, data={"threshold": "0.99"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(image_verification_result().model_dump())
    assert body["decision_threshold"] == 0.55
    assert body["similarity_score"] == score
    assert body["reason_codes"] == [code]
    assert body["success"] is True and body["suggestion"] == "REVIEW"
    assert body["reference"]["usable"] and body["probe"]["usable"]
    assert body["calibration"] == {
        "status": "CONFIGURED_UNVALIDATED", "calibration_id": None, "artifact_sha256": None,
    }


@pytest.mark.parametrize("mutation", [
    "pass", "contradictory_reason", "two_trial_reasons", "missing_score", "failed",
    "side_reason", "quality_reason", "unusable_side", "signature_metadata",
    "uncalibrated", "active", "missing_threshold", "nan_threshold", "string_threshold",
    "bool_score", "failure_fallback",
])
def test_http_revalidates_forged_trial_dto_and_nested_state(algorithm_config, mutation):
    result = _trial_result()
    matched = ReasonCode("IMAGE_TRIAL_THRESHOLD_MATCHED")
    not_matched = ReasonCode("IMAGE_TRIAL_THRESHOLD_NOT_MATCHED")
    if mutation == "pass":
        result = result.model_copy(update={"suggestion": Suggestion.PASS, "reason_codes": ()})
    elif mutation == "contradictory_reason":
        result = result.model_copy(update={"reason_codes": (not_matched,)})
    elif mutation == "two_trial_reasons":
        result = result.model_copy(update={"reason_codes": (matched, not_matched)})
    elif mutation == "missing_score":
        result = result.model_copy(update={"similarity_score": None})
    elif mutation == "failed":
        result = result.model_copy(update={"success": False})
    elif mutation == "side_reason":
        result = result.model_copy(update={"probe": result.probe.model_copy(update={
            "usable": False, "reason_codes": (matched,),
        })})
    elif mutation == "quality_reason":
        from face_algorithm.schemas import QualityResult
        result = result.model_copy(update={"probe": result.probe.model_copy(update={
            "quality": QualityResult(passed=True, reason_codes=(matched,)),
        })})
    elif mutation == "unusable_side":
        result = result.model_copy(update={"probe": result.probe.model_copy(update={"usable": False})})
    elif mutation == "signature_metadata":
        result = result.model_copy(update={"calibration": result.calibration.model_copy(update={
            "calibration_id": "fake", "artifact_sha256": "f" * 64,
        })})
    elif mutation == "uncalibrated":
        result = result.model_copy(update={
            "calibration": ImageCalibrationSummary(status="UNCALIBRATED"), "decision_threshold": None,
        })
    elif mutation == "active":
        result = result.model_copy(update={"calibration": ImageCalibrationSummary(
            status="ACTIVE", calibration_id="fake", artifact_sha256="f" * 64,
        )})
    elif mutation == "missing_threshold":
        result = result.model_copy(update={"decision_threshold": None})
    elif mutation == "nan_threshold":
        result = result.model_copy(update={"decision_threshold": float("nan")})
    elif mutation == "string_threshold":
        result = result.model_copy(update={"decision_threshold": "0.55"})
    elif mutation == "bool_score":
        result = result.model_copy(update={"similarity_score": True})
    elif mutation == "failure_fallback":
        result = result.model_copy(update={
            "success": False, "similarity_score": None,
            "probe": result.probe.model_copy(update={"usable": False, "reason_codes": (matched,)}),
        })

    class Services(FakeServices):
        def verify_images(self, *args, **kwargs):
            return result

    with TestClient(create_app(config=algorithm_config, services=Services())) as client:
        response = _post_images(client)

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False and body["reason_codes"] == ["INFERENCE_ERROR"]
    assert "similarity_score" not in body
    assert "IMAGE_TRIAL_THRESHOLD" not in response.text


def _configured_http_services(algorithm_config, monkeypatch):
    payload = algorithm_config.model_dump(mode="python")
    payload["image_verification"] = {
        "mode": "configured", "threshold": 0.55,
        "reference_quality_profile": "entry_verify", "probe_quality_profile": "entry_verify",
    }
    config = AlgorithmConfig.model_validate(payload)
    roles = (
        "face_detector", "face_landmark_primary", "face_landmark_secondary",
        "face_recognizer", "rotation_classifier", "pose_var", "pose_conv",
    )
    evidence = {
        role: VerifiedModelEvidence(
            name=role, version="test-v1", sha256=f"{index:064x}", size=100,
            backend="onnxruntime", providers=("CPUExecutionProvider",),
            feature_version="recognizer_v1_1024" if role == "face_recognizer" else None,
            output_dimension=1024 if role == "face_recognizer" else None,
        )
        for index, role in enumerate(roles, start=1)
    }

    class Pipeline:
        model_evidence = evidence
        model_versions = {role: item.version for role, item in evidence.items()}
        config_version = config.config_version

        def __init__(self):
            self.calls = 0
            self.during_analysis = None

        def analyze_trusted(self, image, profile):
            self.calls += 1
            if self.during_analysis is not None:
                self.during_analysis()
            vector = np.zeros(1024, dtype=np.float32)
            vector[0] = 1.0
            public = FaceAnalysisResult(
                success=True, suggestion=Suggestion.PASS, reason_codes=(),
                model_versions=self.model_versions, feature_version=FeatureVersion.RECOGNIZER_V1_1024,
                config_version=self.config_version, processing_time_ms=1.0, trace_id="http-runtime-side",
                original_width=image.shape[1], original_height=image.shape[0], rotation_quarter_turns=0,
            )
            return TrustedFaceAnalysis(public=public, embedding=vector, corrected_image=None, aligned_face=None)

    pipeline = Pipeline()
    monkeypatch.setattr("face_algorithm.core.adapters.build_owned_face_pipeline", lambda **kwargs: pipeline)
    registry = SimpleNamespace(
        manifest=SimpleNamespace(models=()),
        health=lambda: HealthStatus(status=HealthLevel.HEALTHY, models=()),
    )
    return config, DefaultAlgorithmServices(config=config, registry=registry), pipeline


@pytest.mark.parametrize("mutation", ["changed_threshold", "nonfinite_threshold", "unissued_copy", "during_analysis"])
def test_real_runtime_state_invalidation_fails_closed_through_http(algorithm_config, monkeypatch, mutation):
    config, services, pipeline = _configured_http_services(algorithm_config, monkeypatch)
    with TestClient(create_app(config=config, services=services), raise_server_exceptions=False) as client:
        valid = _post_images(client)
        assert valid.status_code == 200
        assert valid.json()["calibration"]["status"] == "CONFIGURED_UNVALIDATED"
        assert valid.json()["reason_codes"] == ["IMAGE_TRIAL_THRESHOLD_MATCHED"]
        assert valid.json()["similarity_score"] == 1.0
        state = services._image_verifier._verifier._calibration
        calls_before = pipeline.calls
        if mutation == "changed_threshold":
            object.__setattr__(state, "threshold", 0.6)
        elif mutation == "nonfinite_threshold":
            object.__setattr__(state, "threshold", float("nan"))
        elif mutation == "unissued_copy":
            forged = object.__new__(type(state))
            for item in fields(state):
                object.__setattr__(forged, item.name, getattr(state, item.name))
            object.__setattr__(services._image_verifier._verifier, "_calibration", forged)
        else:
            pipeline.during_analysis = lambda: object.__setattr__(state, "threshold", 0.6)
        rejected = _post_images(client)

    assert rejected.status_code == 500
    assert rejected.json()["reason_codes"] == ["INFERENCE_ERROR"]
    assert rejected.json()["success"] is False
    assert "similarity_score" not in rejected.json()
    assert "IMAGE_TRIAL_THRESHOLD" not in rejected.text
    assert pipeline.calls == calls_before + (2 if mutation == "during_analysis" else 0)
