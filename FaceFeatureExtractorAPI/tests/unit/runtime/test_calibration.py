from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from face_algorithm.runtime.calibration import (
    CalibrationLoadError,
    CalibrationState,
    CalibrationStatus,
    build_pipeline_fingerprint,
    fingerprint_quality_profile,
    load_image_calibration,
)
from face_algorithm.runtime.config import load_config
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
    def __init__(self, keys: dict[str, Ed25519PublicKey]) -> None:
        self.keys = dict(keys)
        self.calls: list[str] = []

    def get_public_key(self, key_id: str) -> Ed25519PublicKey:
        self.calls.append(key_id)
        try:
            return self.keys[key_id]
        except KeyError as error:
            raise KeyError(key_id) from error


def test_calibration_state_cannot_be_constructed_by_callers() -> None:
    with pytest.raises(TypeError):
        CalibrationState(
            status=CalibrationStatus.UNCALIBRATED,
            calibration_id=None,
            threshold=None,
            artifact_sha256=None,
            artifact=None,
        )


def _default_payload() -> dict:
    payload = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_config(path: Path, payload: dict):
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return load_config(path)


def _model_evidence() -> dict[str, VerifiedModelEvidence]:
    result: dict[str, VerifiedModelEvidence] = {}
    for index, name in enumerate(FACE_MODEL_NAMES, start=1):
        recognizer = name == "face_recognizer"
        result[name] = VerifiedModelEvidence(
            name=name,
            version="test-v1",
            sha256=f"{index:064x}",
            size=100 + index,
            backend="onnxruntime",
            providers=("CPUExecutionProvider",),
            feature_version="recognizer_v1_1024" if recognizer else None,
            output_dimension=1024 if recognizer else None,
        )
    return result


def _artifact_payload(config, evidence) -> dict:
    profile = config.quality_profiles.entry_verify
    reference_profile_fingerprint = fingerprint_quality_profile(
        profile,
        role="reference",
    )
    probe_profile_fingerprint = fingerprint_quality_profile(
        profile,
        role="probe",
    )
    return {
        "schema_version": "image_verification_calibration_v1",
        "calibration_id": "supplier-noncustomer-2026q3-v1",
        "scenario": "witness_vs_entry_v1",
        "feature_version": "recognizer_v1_1024",
        "feature_dimension": 1024,
        "matcher_version": "l2_dot_v1",
        "decision_rule_version": "score_gte_threshold_v1",
        "recognizer_sha256": evidence["face_recognizer"].sha256,
        "pipeline_fingerprint": build_pipeline_fingerprint(
            evidence,
            reference_profile_fingerprint=reference_profile_fingerprint,
            probe_profile_fingerprint=probe_profile_fingerprint,
        ),
        "reference_profile_fingerprint": reference_profile_fingerprint,
        "probe_profile_fingerprint": probe_profile_fingerprint,
        "threshold": 0.73,
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


def test_quality_profile_fingerprint_binds_face_selection_strategy(
    tmp_path: Path,
) -> None:
    baseline = load_config(DEFAULT_CONFIG).quality_profiles.portrait
    payload = deepcopy(_default_payload())
    payload["quality_profiles"]["portrait"]["face_selection"][
        "secondary_min_confidence"
    ] = 0.71
    changed = _write_config(tmp_path / "selector-threshold.yaml", payload)

    assert fingerprint_quality_profile(
        baseline,
        role="probe",
    ) != fingerprint_quality_profile(
        changed.quality_profiles.portrait,
        role="probe",
    )


def _signed_artifact(
    tmp_path: Path,
    *,
    mutate_payload=None,
    raw_bytes: bytes | None = None,
):
    base_config = load_config(DEFAULT_CONFIG)
    evidence = _model_evidence()
    payload = _artifact_payload(base_config, evidence)
    if mutate_payload is not None:
        mutate_payload(payload)
    artifact_bytes = raw_bytes or json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    private_key = Ed25519PrivateKey.generate()
    signature = private_key.sign(artifact_bytes)
    artifact_path = tmp_path / "image-calibration.json"
    signature_path = tmp_path / "image-calibration.sig"
    artifact_path.write_bytes(artifact_bytes)
    signature_path.write_bytes(signature)
    config_payload = deepcopy(_default_payload())
    config_payload["image_verification"] = {
        "mode": "artifact",
        "reference_quality_profile": "entry_verify",
        "probe_quality_profile": "entry_verify",
        "artifact_path": str(artifact_path),
        "expected_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "signature_path": str(signature_path),
        "signer_key_id": "test-key-v1",
    }
    config = _write_config(tmp_path / "algorithm.yaml", config_payload)
    keys = StaticKeyStore({"test-key-v1": private_key.public_key()})
    return config, evidence, keys, artifact_path, signature_path


def test_uncalibrated_mode_does_not_touch_keys_or_model_evidence() -> None:
    config = load_config(DEFAULT_CONFIG)
    keys = StaticKeyStore({})

    state = load_image_calibration(
        config.image_verification,
        key_store=keys,
        model_evidence={},
        quality_profiles=config.quality_profiles,
    )

    assert state.status is CalibrationStatus.UNCALIBRATED
    assert state.threshold is None
    assert state.calibration_id is None
    assert state.artifact_sha256 is None
    assert keys.calls == []


def test_valid_signed_artifact_activates_only_after_all_bindings_match(
    tmp_path: Path,
) -> None:
    config, evidence, keys, _, _ = _signed_artifact(tmp_path)

    state = load_image_calibration(
        config.image_verification,
        key_store=keys,
        model_evidence=evidence,
        quality_profiles=config.quality_profiles,
    )

    assert state.status is CalibrationStatus.ACTIVE
    assert state.calibration_id == "supplier-noncustomer-2026q3-v1"
    assert state.threshold == pytest.approx(0.73)
    assert state.artifact_sha256 == config.image_verification.expected_sha256
    assert state.artifact is not None
    with pytest.raises(Exception):
        state.artifact.threshold = 0.1


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("scenario", "different_scenario"),
        ("feature_version", "recognizer_v0_1024"),
        ("feature_dimension", 512),
        ("matcher_version", "cosine_v0"),
        ("decision_rule_version", "score_gt_threshold_v0"),
        ("recognizer_sha256", "e" * 64),
        ("pipeline_fingerprint", "e" * 64),
        ("reference_profile_fingerprint", "e" * 64),
        ("probe_profile_fingerprint", "e" * 64),
    ],
)
def test_any_contract_binding_mismatch_fails_startup(
    tmp_path: Path,
    field: str,
    new_value,
) -> None:
    config, evidence, keys, _, _ = _signed_artifact(
        tmp_path,
        mutate_payload=lambda payload: payload.__setitem__(field, new_value),
    )

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


@pytest.mark.parametrize("forbidden_field", ["images", "embedding", "records"])
def test_personal_or_biometric_payload_fields_are_forbidden(
    tmp_path: Path,
    forbidden_field: str,
) -> None:
    config, evidence, keys, _, _ = _signed_artifact(
        tmp_path,
        mutate_payload=lambda payload: payload.__setitem__(forbidden_field, []),
    )

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


@pytest.mark.parametrize("value", [False, None])
def test_non_customer_evidence_attestation_is_required_and_must_be_true(
    tmp_path: Path,
    value,
) -> None:
    def mutate(payload: dict) -> None:
        if value is None:
            payload["evidence"].pop("non_customer_data")
        else:
            payload["evidence"]["non_customer_data"] = value

    config, evidence, keys, _, _ = _signed_artifact(
        tmp_path,
        mutate_payload=mutate,
    )

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unknown_evidence", "forbidden"),
        ("genuine_pairs", True),
        ("impostor_pairs", "10000"),
        ("target_far", -0.01),
        ("measured_far", 1.01),
        ("measured_frr", float("nan")),
        ("target_far", float("inf")),
    ],
)
def test_calibration_evidence_is_strict_and_bounded(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    config, evidence, keys, _, _ = _signed_artifact(
        tmp_path,
        mutate_payload=lambda payload: payload["evidence"].__setitem__(field, value),
    )

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


@pytest.mark.parametrize("value", [True, "0.73", -0.01, 1.01, float("nan"), float("inf")])
def test_calibration_threshold_is_strict_finite_and_bounded(
    tmp_path: Path,
    value,
) -> None:
    config, evidence, keys, _, _ = _signed_artifact(
        tmp_path,
        mutate_payload=lambda payload: payload.__setitem__("threshold", value),
    )

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


@pytest.mark.parametrize(
    "raw_bytes",
    [
        b'{"schema_version":"x","schema_version":"y"}',
        b'{"threshold":NaN}',
        b"\xff\xfe\x00",
        b"x" * (64 * 1024 + 1),
    ],
    ids=["duplicate", "nan", "invalid-utf8", "oversize"],
)
def test_malformed_duplicate_nonfinite_or_oversize_artifact_fails_startup(
    tmp_path: Path,
    raw_bytes: bytes,
) -> None:
    config, evidence, keys, _, _ = _signed_artifact(tmp_path, raw_bytes=raw_bytes)

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


def test_hash_or_signature_or_unknown_key_failure_never_downgrades(
    tmp_path: Path,
) -> None:
    config, evidence, keys, artifact_path, signature_path = _signed_artifact(tmp_path)
    original_signature = signature_path.read_bytes()

    artifact_path.write_bytes(artifact_path.read_bytes() + b" ")
    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )

    artifact_path.write_bytes(artifact_path.read_bytes()[:-1])
    signature_path.write_bytes(b"0" * len(original_signature))
    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )

    signature_path.write_bytes(original_signature)
    unknown_keys = StaticKeyStore({})
    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=unknown_keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


@pytest.mark.parametrize(
    ("missing_target", "signature_bytes"),
    [
        ("artifact", None),
        ("signature", None),
        (None, b"x" * 63),
        (None, b"x" * 65),
        (None, b"x" * (64 * 1024 + 1)),
    ],
    ids=["missing-artifact", "missing-signature", "short", "long", "oversize"],
)
def test_missing_or_invalid_length_calibration_files_fail_startup(
    tmp_path: Path,
    missing_target: str | None,
    signature_bytes: bytes | None,
) -> None:
    config, evidence, keys, artifact_path, signature_path = _signed_artifact(tmp_path)
    if missing_target == "artifact":
        artifact_path.unlink()
    elif missing_target == "signature":
        signature_path.unlink()
    elif signature_bytes is not None:
        signature_path.write_bytes(signature_bytes)

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=evidence,
            quality_profiles=config.quality_profiles,
        )


def test_runtime_recognizer_evidence_must_be_proven_and_exact(tmp_path: Path) -> None:
    config, evidence, keys, _, _ = _signed_artifact(tmp_path)
    replaced = dict(evidence)
    replaced["face_recognizer"] = VerifiedModelEvidence(
        name="face_recognizer",
        version="replacement",
        sha256="f" * 64,
        size=999,
        backend="onnxruntime",
        providers=("CPUExecutionProvider",),
        feature_version="recognizer_v1_1024",
        output_dimension=1024,
    )

    with pytest.raises(CalibrationLoadError):
        load_image_calibration(
            config.image_verification,
            key_store=keys,
            model_evidence=replaced,
            quality_profiles=config.quality_profiles,
        )


def test_pipeline_fingerprint_binds_actual_provider_snapshot() -> None:
    config = load_config(DEFAULT_CONFIG)
    evidence = _model_evidence()
    profile = config.quality_profiles.entry_verify
    reference_fingerprint = fingerprint_quality_profile(profile, role="reference")
    probe_fingerprint = fingerprint_quality_profile(profile, role="probe")

    actual_cpu = build_pipeline_fingerprint(
        evidence,
        reference_profile_fingerprint=reference_fingerprint,
        probe_profile_fingerprint=probe_fingerprint,
    )
    requested_cuda = dict(evidence)
    detector = evidence["face_detector"]
    requested_cuda["face_detector"] = VerifiedModelEvidence(
        name=detector.name,
        version=detector.version,
        sha256=detector.sha256,
        size=detector.size,
        backend=detector.backend,
        providers=("CUDAExecutionProvider", "CPUExecutionProvider"),
        feature_version=detector.feature_version,
        output_dimension=detector.output_dimension,
    )

    assert actual_cpu != build_pipeline_fingerprint(
        requested_cuda,
        reference_profile_fingerprint=reference_fingerprint,
        probe_profile_fingerprint=probe_fingerprint,
    )
