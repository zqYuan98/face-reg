from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from face_algorithm.runtime.calibration import (
    CalibrationState,
    build_pipeline_fingerprint,
    fingerprint_quality_profile,
    load_image_calibration,
)
from face_algorithm.runtime.config import AlgorithmConfig, load_config
from face_algorithm.runtime.model_registry import VerifiedModelEvidence


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "algorithm.yaml"
FACE_MODEL_NAMES = (
    "face_detector",
    "face_landmark_primary",
    "face_landmark_secondary",
    "face_recognizer",
    "rotation_classifier",
    "pose_var",
    "pose_conv",
)


class StaticKeyStore:
    def __init__(self, key_id: str, key: Ed25519PublicKey) -> None:
        self.key_id = key_id
        self.key = key

    def get_public_key(self, key_id: str) -> Ed25519PublicKey:
        if key_id != self.key_id:
            raise KeyError(key_id)
        return self.key


@dataclass(frozen=True, slots=True)
class ActiveCalibrationBundle:
    state: CalibrationState
    config: AlgorithmConfig
    key_store: StaticKeyStore
    evidence: dict[str, VerifiedModelEvidence]


def _model_evidence() -> dict[str, VerifiedModelEvidence]:
    result: dict[str, VerifiedModelEvidence] = {}
    for index, role in enumerate(FACE_MODEL_NAMES, start=1):
        recognizer = role == "face_recognizer"
        result[role] = VerifiedModelEvidence(
            name=role,
            version="test-v1",
            sha256=f"{index:064x}",
            size=100 + index,
            backend="onnxruntime",
            providers=("CPUExecutionProvider",),
            feature_version="recognizer_v1_1024" if recognizer else None,
            output_dimension=1024 if recognizer else None,
        )
    return result


@pytest.fixture
def active_calibration_bundle(tmp_path: Path) -> ActiveCalibrationBundle:
    base_config = load_config(DEFAULT_CONFIG)
    profile = base_config.quality_profiles.entry_verify
    reference_fingerprint = fingerprint_quality_profile(profile, role="reference")
    probe_fingerprint = fingerprint_quality_profile(profile, role="probe")
    evidence = _model_evidence()
    payload = {
        "schema_version": "image_verification_calibration_v1",
        "calibration_id": "signed-test-calibration-v1",
        "scenario": "witness_vs_entry_v1",
        "feature_version": "recognizer_v1_1024",
        "feature_dimension": 1024,
        "matcher_version": "l2_dot_v1",
        "decision_rule_version": "score_gte_threshold_v1",
        "recognizer_sha256": evidence["face_recognizer"].sha256,
        "pipeline_fingerprint": build_pipeline_fingerprint(
            evidence,
            reference_profile_fingerprint=reference_fingerprint,
            probe_profile_fingerprint=probe_fingerprint,
        ),
        "reference_profile_fingerprint": reference_fingerprint,
        "probe_profile_fingerprint": probe_fingerprint,
        "threshold": 0.75,
        "evidence": {
            "provenance": "licensed_non_customer",
            "non_customer_data": True,
            "method": "subject_disjoint_roc_v1",
            "subject_disjoint": True,
            "dataset_fingerprint": "d" * 64,
            "genuine_pairs": 1000,
            "impostor_pairs": 10000,
            "target_far": 0.001,
            "measured_far": 0.0009,
            "measured_frr": 0.02,
            "generated_at_utc": "2026-08-04T00:00:00Z",
        },
    }
    artifact_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    private_key = Ed25519PrivateKey.generate()
    artifact_path = tmp_path / "calibration.json"
    signature_path = tmp_path / "calibration.sig"
    artifact_path.write_bytes(artifact_bytes)
    signature_path.write_bytes(private_key.sign(artifact_bytes))

    config_payload = deepcopy(
        yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    )
    config_payload["image_verification"] = {
        "mode": "artifact",
        "reference_quality_profile": "entry_verify",
        "probe_quality_profile": "entry_verify",
        "artifact_path": str(artifact_path),
        "expected_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "signature_path": str(signature_path),
        "signer_key_id": "test-signer-v1",
    }
    config_path = tmp_path / "algorithm.yaml"
    config_path.write_text(
        yaml.safe_dump(config_payload, sort_keys=False),
        encoding="utf-8",
    )
    config = load_config(config_path)
    key_store = StaticKeyStore("test-signer-v1", private_key.public_key())
    state = load_image_calibration(
        config.image_verification,
        key_store=key_store,
        model_evidence=evidence,
        quality_profiles=config.quality_profiles,
    )
    return ActiveCalibrationBundle(
        state=state,
        config=config,
        key_store=key_store,
        evidence=evidence,
    )


@pytest.fixture
def active_calibration(
    active_calibration_bundle: ActiveCalibrationBundle,
) -> CalibrationState:
    return active_calibration_bundle.state


@pytest.fixture
def uncalibrated_calibration() -> CalibrationState:
    config = load_config(DEFAULT_CONFIG)
    return load_image_calibration(
        config.image_verification,
        key_store=None,
        model_evidence={},
        quality_profiles=config.quality_profiles,
    )
