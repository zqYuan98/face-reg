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
    build_pipeline_fingerprint,
    fingerprint_quality_profile,
    fingerprint_video_policy,
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


class VideoKeyStore:
    def __init__(self, key: Ed25519PublicKey) -> None:
        self._key = key

    def get_public_key(self, key_id: str) -> Ed25519PublicKey:
        if key_id != "video-release-v1":
            raise KeyError(key_id)
        return self._key


@dataclass(frozen=True, slots=True)
class ActiveVideoCalibrationBundle:
    config: AlgorithmConfig
    key_store: VideoKeyStore
    evidence: dict[str, VerifiedModelEvidence]


def video_model_evidence() -> dict[str, VerifiedModelEvidence]:
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
def active_video_calibration_bundle(tmp_path: Path) -> ActiveVideoCalibrationBundle:
    base = load_config(DEFAULT_CONFIG)
    evidence = video_model_evidence()
    reference_fingerprint = fingerprint_quality_profile(
        base.quality_profiles.contract_reference,
        role="reference",
    )
    frame_fingerprint = fingerprint_quality_profile(
        base.quality_profiles.contract_video_frame,
        role="probe",
    )
    payload = {
        "schema_version": "video_verification_calibration_v1",
        "calibration_id": "signed-video-test-v1",
        "scenario": "contract_video_v1",
        "feature_version": "recognizer_v1_1024",
        "feature_dimension": 1024,
        "matcher_version": "l2_dot_v1",
        "decision_rule_version": "video_track_aggregate_v1",
        "recognizer_sha256": evidence["face_recognizer"].sha256,
        "pipeline_fingerprint": build_pipeline_fingerprint(
            evidence,
            reference_profile_fingerprint=reference_fingerprint,
            probe_profile_fingerprint=frame_fingerprint,
            scenario="contract_video_v1",
            decision_rule_version="video_track_aggregate_v1",
        ),
        "reference_profile_fingerprint": reference_fingerprint,
        "frame_profile_fingerprint": frame_fingerprint,
        "policy_fingerprint": fingerprint_video_policy(base.video_verification),
        "frame_threshold": 0.72,
        "quick_aggregate_threshold": 0.75,
        "quick_min_pass_ratio": 0.70,
        "full_aggregate_threshold": 0.77,
        "full_min_pass_ratio": 0.80,
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
    artifact_path = tmp_path / "video-calibration.json"
    signature_path = tmp_path / "video-calibration.sig"
    artifact_path.write_bytes(artifact_bytes)
    signature_path.write_bytes(private_key.sign(artifact_bytes))

    config_payload = deepcopy(yaml.safe_load(DEFAULT_CONFIG.read_text("utf-8")))
    config_payload["video_verification"]["calibration"] = {
        "mode": "artifact",
        "artifact_path": str(artifact_path),
        "expected_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "signature_path": str(signature_path),
        "signer_key_id": "video-release-v1",
    }
    config_path = tmp_path / "algorithm.yaml"
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), "utf-8")
    return ActiveVideoCalibrationBundle(
        config=load_config(config_path),
        key_store=VideoKeyStore(private_key.public_key()),
        evidence=evidence,
    )
