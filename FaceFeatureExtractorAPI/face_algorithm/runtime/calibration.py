"""Fail-closed loading of signed identity-verification calibration artifacts."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from face_algorithm.core.matcher import MATCHER_VERSION
from face_algorithm.runtime.config import (
    ImageVerificationConfig,
    QualityProfiles,
    VideoVerificationPolicy,
)
from face_algorithm.runtime.model_registry import VerifiedModelEvidence
from face_algorithm.schemas import FEATURE_DIMENSION, FeatureVersion

__all__ = [
    "CalibrationLoadError",
    "CalibrationState",
    "CalibrationStatus",
    "ImageCalibrationArtifact",
    "VideoCalibrationArtifact",
    "VideoCalibrationThresholds",
    "TrustedCalibrationKeyStore",
    "build_pipeline_fingerprint",
    "fingerprint_quality_profile",
    "fingerprint_video_policy",
    "load_image_calibration",
    "load_video_calibration",
]

MAX_ARTIFACT_BYTES = 64 * 1024
CALIBRATION_SCHEMA_VERSION = "image_verification_calibration_v1"
CALIBRATION_SCENARIO = "witness_vs_entry_v1"
DECISION_RULE_VERSION = "score_gte_threshold_v1"
VIDEO_CALIBRATION_SCHEMA_VERSION = "video_verification_calibration_v1"
VIDEO_CALIBRATION_SCENARIO = "contract_video_v1"
VIDEO_DECISION_RULE_VERSION = "video_track_aggregate_v1"
PIPELINE_CONTRACT_VERSION = "face_pipeline_v1"
QUALITY_PROFILE_CONTRACT_VERSION = "quality_profile_v1"
_FACE_MODEL_NAMES = (
    "face_detector",
    "face_landmark_primary",
    "face_landmark_secondary",
    "face_recognizer",
    "rotation_classifier",
    "pose_var",
    "pose_conv",
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_FORBIDDEN_BIOMETRIC_FIELDS = frozenset(
    {"image", "images", "embedding", "embeddings", "record", "records"}
)


class CalibrationLoadError(RuntimeError):
    """The configured calibration cannot safely become active."""


class CalibrationStatus(str, Enum):
    UNCALIBRATED = "UNCALIBRATED"
    ACTIVE = "ACTIVE"


class TrustedCalibrationKeyStore(Protocol):
    """Independent trust store for calibration release public keys."""

    def get_public_key(self, key_id: str) -> Ed25519PublicKey: ...


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class CalibrationEvidence(_StrictFrozenModel):
    provenance: Literal["licensed_non_customer"]
    non_customer_data: Literal[True]
    method: Literal["subject_disjoint_roc_v1"]
    subject_disjoint: Literal[True]
    dataset_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    genuine_pairs: int = Field(gt=0)
    impostor_pairs: int = Field(gt=0)
    target_far: float = Field(ge=0, le=1, allow_inf_nan=False)
    measured_far: float = Field(ge=0, le=1, allow_inf_nan=False)
    measured_frr: float = Field(ge=0, le=1, allow_inf_nan=False)
    generated_at_utc: str = Field(
        min_length=20,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$",
    )

    @field_validator("non_customer_data", "subject_disjoint", mode="before")
    @classmethod
    def require_literal_true(cls, value: Any) -> Any:
        if type(value) is not bool or value is not True:
            raise ValueError("evidence attestation must be the boolean true")
        return value

    @field_validator("genuine_pairs", "impostor_pairs", mode="before")
    @classmethod
    def reject_boolean_pair_counts(cls, value: Any) -> Any:
        if type(value) is bool:
            raise ValueError("pair counts must be integers, not booleans")
        return value

    @field_validator(
        "target_far",
        "measured_far",
        "measured_frr",
        mode="before",
    )
    @classmethod
    def reject_boolean_rates(cls, value: Any) -> Any:
        if type(value) is bool:
            raise ValueError("rates must be numbers, not booleans")
        return value


class ImageCalibrationArtifact(_StrictFrozenModel):
    schema_version: Literal["image_verification_calibration_v1"]
    calibration_id: str = Field(min_length=1, max_length=128)
    scenario: Literal["witness_vs_entry_v1"]
    feature_version: Literal["recognizer_v1_1024"]
    feature_dimension: Literal[1024]
    matcher_version: Literal["l2_dot_v1"]
    decision_rule_version: Literal["score_gte_threshold_v1"]
    recognizer_sha256: str = Field(pattern=_SHA256_PATTERN)
    pipeline_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    reference_profile_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    probe_profile_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: CalibrationEvidence

    @field_validator("feature_dimension", mode="before")
    @classmethod
    def reject_boolean_dimension(cls, value: Any) -> Any:
        if type(value) is bool:
            raise ValueError("feature dimension must be an integer")
        return value

    @field_validator("threshold", mode="before")
    @classmethod
    def reject_boolean_threshold(cls, value: Any) -> Any:
        if type(value) is bool:
            raise ValueError("threshold must be a number, not a boolean")
        return value


class VideoCalibrationArtifact(_StrictFrozenModel):
    schema_version: Literal["video_verification_calibration_v1"]
    calibration_id: str = Field(min_length=1, max_length=128)
    scenario: Literal["contract_video_v1"]
    feature_version: Literal["recognizer_v1_1024"]
    feature_dimension: Literal[1024]
    matcher_version: Literal["l2_dot_v1"]
    decision_rule_version: Literal["video_track_aggregate_v1"]
    recognizer_sha256: str = Field(pattern=_SHA256_PATTERN)
    pipeline_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    reference_profile_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    frame_profile_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    policy_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    frame_threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    quick_aggregate_threshold: float = Field(ge=-1, le=1, allow_inf_nan=False)
    quick_min_pass_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    full_aggregate_threshold: float = Field(ge=-1, le=1, allow_inf_nan=False)
    full_min_pass_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: CalibrationEvidence

    @field_validator("feature_dimension", mode="before")
    @classmethod
    def reject_boolean_dimension(cls, value: Any) -> Any:
        if type(value) is bool:
            raise ValueError("feature dimension must be an integer")
        return value

    @field_validator(
        "frame_threshold",
        "quick_aggregate_threshold",
        "quick_min_pass_ratio",
        "full_aggregate_threshold",
        "full_min_pass_ratio",
        mode="before",
    )
    @classmethod
    def reject_boolean_thresholds(cls, value: Any) -> Any:
        if type(value) is bool:
            raise ValueError("thresholds must be numbers, not booleans")
        return value


@dataclass(frozen=True, slots=True)
class VideoCalibrationThresholds:
    frame_threshold: float
    quick_aggregate_threshold: float
    quick_min_pass_ratio: float
    full_aggregate_threshold: float
    full_min_pass_ratio: float


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class CalibrationState:
    status: CalibrationStatus
    calibration_id: str | None
    threshold: float | None
    artifact_sha256: str | None
    artifact: ImageCalibrationArtifact | VideoCalibrationArtifact | None
    video_thresholds: VideoCalibrationThresholds | None
    _capability: object = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("CalibrationState can only be created by the calibration loader")


_CALIBRATION_CAPABILITY = object()
_CALIBRATION_ISSUANCE_LOCK = threading.Lock()
_ISSUED_CALIBRATIONS: weakref.WeakSet[CalibrationState] = weakref.WeakSet()


def _issue_calibration_state(
    *,
    status: CalibrationStatus,
    calibration_id: str | None,
    threshold: float | None,
    artifact_sha256: str | None,
    artifact: ImageCalibrationArtifact | VideoCalibrationArtifact | None,
    video_thresholds: VideoCalibrationThresholds | None = None,
) -> CalibrationState:
    state = object.__new__(CalibrationState)
    object.__setattr__(state, "status", status)
    object.__setattr__(state, "calibration_id", calibration_id)
    object.__setattr__(state, "threshold", threshold)
    object.__setattr__(state, "artifact_sha256", artifact_sha256)
    object.__setattr__(state, "artifact", artifact)
    object.__setattr__(state, "video_thresholds", video_thresholds)
    object.__setattr__(state, "_capability", _CALIBRATION_CAPABILITY)
    with _CALIBRATION_ISSUANCE_LOCK:
        _ISSUED_CALIBRATIONS.add(state)
    return state


def _is_loader_issued_calibration(state: object) -> bool:
    if not isinstance(state, CalibrationState):
        return False
    try:
        capability = state._capability
    except AttributeError:
        return False
    if capability is not _CALIBRATION_CAPABILITY:
        return False
    with _CALIBRATION_ISSUANCE_LOCK:
        return state in _ISSUED_CALIBRATIONS


def _canonical_json_sha256(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CalibrationLoadError("calibration fingerprint payload is invalid") from error
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_quality_profile(
    profile: BaseModel,
    *,
    role: Literal["reference", "probe"],
) -> str:
    """Fingerprint one role-bound, immutable quality profile."""

    if not isinstance(profile, BaseModel):
        raise CalibrationLoadError("quality profile must be a validated model")
    return _canonical_json_sha256(
        {
            "contract_version": QUALITY_PROFILE_CONTRACT_VERSION,
            "role": role,
            "profile": profile.model_dump(mode="json"),
        }
    )


def build_pipeline_fingerprint(
    model_evidence: Mapping[str, VerifiedModelEvidence],
    *,
    reference_profile_fingerprint: str,
    probe_profile_fingerprint: str,
    scenario: Literal["witness_vs_entry_v1", "contract_video_v1"] = CALIBRATION_SCENARIO,
    decision_rule_version: Literal[
        "score_gte_threshold_v1",
        "video_track_aggregate_v1",
    ] = DECISION_RULE_VERSION,
) -> str:
    """Bind exact loaded model evidence and all image-verification contracts."""

    if set(model_evidence) != set(_FACE_MODEL_NAMES):
        raise CalibrationLoadError("pipeline evidence must contain exactly seven face roles")
    if not _is_sha256(reference_profile_fingerprint) or not _is_sha256(
        probe_profile_fingerprint
    ):
        raise CalibrationLoadError("quality profile fingerprint is invalid")

    models: list[dict[str, Any]] = []
    for role in _FACE_MODEL_NAMES:
        evidence = model_evidence.get(role)
        if not isinstance(evidence, VerifiedModelEvidence):
            raise CalibrationLoadError(f"verified model evidence is invalid for {role}")
        if evidence.name != role or not _is_sha256(evidence.sha256):
            raise CalibrationLoadError(f"verified model identity is invalid for {role}")
        if (
            not evidence.version
            or evidence.size <= 0
            or not evidence.backend
            or not evidence.providers
            or any(not provider for provider in evidence.providers)
        ):
            raise CalibrationLoadError(f"verified model metadata is invalid for {role}")
        if role == "face_recognizer":
            if (
                evidence.feature_version
                != FeatureVersion.RECOGNIZER_V1_1024.value
                or evidence.output_dimension != FEATURE_DIMENSION
            ):
                raise CalibrationLoadError("recognizer runtime contract is invalid")
        elif evidence.feature_version is not None:
            raise CalibrationLoadError("only recognizer may declare a feature version")
        models.append(
            {
                "role": role,
                "name": evidence.name,
                "version": evidence.version,
                "sha256": evidence.sha256,
                "size": evidence.size,
                "backend": evidence.backend,
                "providers": list(evidence.providers),
                "feature_version": evidence.feature_version,
                "output_dimension": evidence.output_dimension,
            }
        )

    return _canonical_json_sha256(
        {
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "scenario": scenario,
            "feature_version": FeatureVersion.RECOGNIZER_V1_1024.value,
            "feature_dimension": FEATURE_DIMENSION,
            "matcher_version": MATCHER_VERSION,
            "decision_rule_version": decision_rule_version,
            "reference_profile_fingerprint": reference_profile_fingerprint,
            "probe_profile_fingerprint": probe_profile_fingerprint,
            "models": models,
        }
    )


def fingerprint_video_policy(policy: VideoVerificationPolicy) -> str:
    """Bind sampling, aggregation, tracking and profile-role configuration."""

    if not isinstance(policy, VideoVerificationPolicy):
        raise CalibrationLoadError("video policy must be a validated model")
    return _canonical_json_sha256(
        {
            "contract_version": "video_verification_policy_v1",
            "policy": policy.model_dump(mode="json", exclude={"calibration"}),
        }
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_once(path: Path, *, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as error:
        raise CalibrationLoadError(f"{label} file is unavailable") from error
    if not data:
        raise CalibrationLoadError(f"{label} file is empty")
    if len(data) > limit:
        raise CalibrationLoadError(f"{label} file exceeds its size limit")
    return data


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _reject_biometric_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in _FORBIDDEN_BIOMETRIC_FIELDS:
                raise CalibrationLoadError("calibration artifact contains biometric data")
            _reject_biometric_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_biometric_fields(nested)


def _strict_artifact_payload(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CalibrationLoadError("calibration artifact is not strict UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise CalibrationLoadError("calibration artifact root must be an object")
    _reject_biometric_fields(payload)
    return payload


def _parse_artifact(data: bytes) -> ImageCalibrationArtifact:
    try:
        return ImageCalibrationArtifact.model_validate(_strict_artifact_payload(data))
    except ValidationError as error:
        raise CalibrationLoadError("calibration artifact schema is invalid") from error


def _parse_video_artifact(data: bytes) -> VideoCalibrationArtifact:
    try:
        return VideoCalibrationArtifact.model_validate(_strict_artifact_payload(data))
    except ValidationError as error:
        raise CalibrationLoadError("calibration artifact schema is invalid") from error


def _verify_signature(
    *,
    data: bytes,
    signature: bytes,
    signer_key_id: str,
    key_store: TrustedCalibrationKeyStore | None,
) -> None:
    if len(signature) != 64:
        raise CalibrationLoadError("Ed25519 signature must be exactly 64 bytes")
    if key_store is None:
        raise CalibrationLoadError("trusted calibration key store is required")
    try:
        public_key = key_store.get_public_key(signer_key_id)
    except Exception as error:
        raise CalibrationLoadError("calibration signer key is not trusted") from error
    if not isinstance(public_key, Ed25519PublicKey):
        raise CalibrationLoadError("trusted calibration key has the wrong type")
    try:
        public_key.verify(signature, data)
    except InvalidSignature as error:
        raise CalibrationLoadError("calibration signature verification failed") from error
    except Exception as error:
        raise CalibrationLoadError("calibration signature could not be verified") from error


def _load_signed_artifact_bytes(
    config: Any,
    *,
    key_store: TrustedCalibrationKeyStore | None,
) -> tuple[bytes, str]:
    """Read, digest-check and authenticate one artifact exactly once."""

    artifact_bytes = _read_once(
        config.artifact_path,
        limit=MAX_ARTIFACT_BYTES,
        label="calibration artifact",
    )
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    if not hmac.compare_digest(artifact_sha256, config.expected_sha256):
        raise CalibrationLoadError("calibration artifact SHA-256 does not match")
    signature = _read_once(
        config.signature_path,
        limit=64,
        label="calibration signature",
    )
    _verify_signature(
        data=artifact_bytes,
        signature=signature,
        signer_key_id=config.signer_key_id,
        key_store=key_store,
    )
    return artifact_bytes, artifact_sha256


def _require_binding(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise CalibrationLoadError(f"calibration {label} binding does not match runtime")


def load_image_calibration(
    config: ImageVerificationConfig,
    *,
    key_store: TrustedCalibrationKeyStore | None,
    model_evidence: Mapping[str, VerifiedModelEvidence],
    quality_profiles: QualityProfiles,
) -> CalibrationState:
    """Resolve one immutable startup-time calibration state without downgrade."""

    if config.mode == "uncalibrated":
        return _issue_calibration_state(
            status=CalibrationStatus.UNCALIBRATED,
            calibration_id=None,
            threshold=None,
            artifact_sha256=None,
            artifact=None,
        )
    if config.mode != "artifact":
        raise CalibrationLoadError("unsupported image calibration mode")

    artifact_bytes, artifact_sha256 = _load_signed_artifact_bytes(
        config,
        key_store=key_store,
    )
    artifact = _parse_artifact(artifact_bytes)

    try:
        reference_profile = getattr(
            quality_profiles,
            config.reference_quality_profile,
        )
        probe_profile = getattr(quality_profiles, config.probe_quality_profile)
    except AttributeError as error:
        raise CalibrationLoadError("configured quality profile is unavailable") from error
    reference_fingerprint = fingerprint_quality_profile(
        reference_profile,
        role="reference",
    )
    probe_fingerprint = fingerprint_quality_profile(probe_profile, role="probe")
    pipeline_fingerprint = build_pipeline_fingerprint(
        model_evidence,
        reference_profile_fingerprint=reference_fingerprint,
        probe_profile_fingerprint=probe_fingerprint,
    )
    recognizer_evidence = model_evidence["face_recognizer"]

    _require_binding(artifact.schema_version, CALIBRATION_SCHEMA_VERSION, "schema")
    _require_binding(artifact.scenario, CALIBRATION_SCENARIO, "scenario")
    _require_binding(
        artifact.feature_version,
        FeatureVersion.RECOGNIZER_V1_1024.value,
        "feature version",
    )
    _require_binding(artifact.feature_dimension, FEATURE_DIMENSION, "feature dimension")
    _require_binding(artifact.matcher_version, MATCHER_VERSION, "matcher")
    _require_binding(
        artifact.decision_rule_version,
        DECISION_RULE_VERSION,
        "decision rule",
    )
    _require_binding(
        artifact.recognizer_sha256,
        recognizer_evidence.sha256,
        "recognizer digest",
    )
    _require_binding(
        artifact.pipeline_fingerprint,
        pipeline_fingerprint,
        "pipeline fingerprint",
    )
    _require_binding(
        artifact.reference_profile_fingerprint,
        reference_fingerprint,
        "reference profile",
    )
    _require_binding(
        artifact.probe_profile_fingerprint,
        probe_fingerprint,
        "probe profile",
    )

    return _issue_calibration_state(
        status=CalibrationStatus.ACTIVE,
        calibration_id=artifact.calibration_id,
        threshold=artifact.threshold,
        artifact_sha256=artifact_sha256,
        artifact=artifact,
    )


def load_video_calibration(
    policy: VideoVerificationPolicy,
    *,
    key_store: TrustedCalibrationKeyStore | None,
    model_evidence: Mapping[str, VerifiedModelEvidence],
    quality_profiles: QualityProfiles,
) -> CalibrationState:
    """Resolve a video calibration bound to models, profiles and full policy."""

    config = policy.calibration
    if config.mode == "uncalibrated":
        return _issue_calibration_state(
            status=CalibrationStatus.UNCALIBRATED,
            calibration_id=None,
            threshold=None,
            artifact_sha256=None,
            artifact=None,
        )
    if config.mode != "artifact":
        raise CalibrationLoadError("unsupported video calibration mode")

    artifact_bytes, artifact_sha256 = _load_signed_artifact_bytes(
        config,
        key_store=key_store,
    )
    artifact = _parse_video_artifact(artifact_bytes)

    try:
        reference_profile = getattr(
            quality_profiles,
            policy.reference_quality_profile,
        )
        frame_profile = getattr(quality_profiles, policy.frame_quality_profile)
    except AttributeError as error:
        raise CalibrationLoadError("configured quality profile is unavailable") from error
    reference_fingerprint = fingerprint_quality_profile(
        reference_profile,
        role="reference",
    )
    frame_fingerprint = fingerprint_quality_profile(frame_profile, role="probe")
    pipeline_fingerprint = build_pipeline_fingerprint(
        model_evidence,
        reference_profile_fingerprint=reference_fingerprint,
        probe_profile_fingerprint=frame_fingerprint,
        scenario=VIDEO_CALIBRATION_SCENARIO,
        decision_rule_version=VIDEO_DECISION_RULE_VERSION,
    )
    policy_fingerprint = fingerprint_video_policy(policy)
    try:
        recognizer_evidence = model_evidence["face_recognizer"]
    except KeyError as error:
        raise CalibrationLoadError("recognizer evidence is unavailable") from error

    _require_binding(
        artifact.schema_version,
        VIDEO_CALIBRATION_SCHEMA_VERSION,
        "schema",
    )
    _require_binding(artifact.scenario, VIDEO_CALIBRATION_SCENARIO, "scenario")
    _require_binding(
        artifact.feature_version,
        FeatureVersion.RECOGNIZER_V1_1024.value,
        "feature version",
    )
    _require_binding(artifact.feature_dimension, FEATURE_DIMENSION, "feature dimension")
    _require_binding(artifact.matcher_version, MATCHER_VERSION, "matcher")
    _require_binding(
        artifact.decision_rule_version,
        VIDEO_DECISION_RULE_VERSION,
        "decision rule",
    )
    _require_binding(
        artifact.recognizer_sha256,
        recognizer_evidence.sha256,
        "recognizer digest",
    )
    _require_binding(
        artifact.pipeline_fingerprint,
        pipeline_fingerprint,
        "pipeline fingerprint",
    )
    _require_binding(
        artifact.reference_profile_fingerprint,
        reference_fingerprint,
        "reference profile",
    )
    _require_binding(
        artifact.frame_profile_fingerprint,
        frame_fingerprint,
        "frame profile",
    )
    _require_binding(
        artifact.policy_fingerprint,
        policy_fingerprint,
        "policy",
    )

    thresholds = VideoCalibrationThresholds(
        frame_threshold=float(artifact.frame_threshold),
        quick_aggregate_threshold=float(artifact.quick_aggregate_threshold),
        quick_min_pass_ratio=float(artifact.quick_min_pass_ratio),
        full_aggregate_threshold=float(artifact.full_aggregate_threshold),
        full_min_pass_ratio=float(artifact.full_min_pass_ratio),
    )
    return _issue_calibration_state(
        status=CalibrationStatus.ACTIVE,
        calibration_id=artifact.calibration_id,
        threshold=thresholds.frame_threshold,
        artifact_sha256=artifact_sha256,
        artifact=artifact,
        video_thresholds=thresholds,
    )
