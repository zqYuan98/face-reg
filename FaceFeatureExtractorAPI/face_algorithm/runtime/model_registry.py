"""Strict manifests and deadlock-resistant process-level model loading."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from face_algorithm.schemas import (
    FEATURE_DIMENSION,
    FeatureVersion,
    HealthLevel,
    HealthStatus,
    ModelHealth,
    ModelState,
    ReasonCode,
)

__all__ = [
    "Manifest",
    "ManifestModel",
    "LoadedModelHandle",
    "ModelContract",
    "ModelContractError",
    "ModelIntegrityError",
    "ModelLoadError",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelUnavailableError",
    "VerifiedArtifact",
    "VerifiedModelEvidence",
    "load_manifest",
    "verify_manifest_artifacts",
]

MANIFEST_VERSION = "2026-08-04-v1"
_REQUIRED_MODEL_NAMES = frozenset(
    {
        "face_detector",
        "face_landmark_primary",
        "face_landmark_secondary",
        "face_recognizer",
        "rotation_classifier",
        "pose_var",
        "pose_conv",
    }
)
_OPTIONAL_MODEL_NAMES = frozenset({"portrait_matting"})
_EXPECTED_MODEL_NAMES = _REQUIRED_MODEL_NAMES | _OPTIONAL_MODEL_NAMES


class _ManifestSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class ExpectedOutput(_ManifestSchema):
    output_dimension: int | None = Field(default=None, gt=0)


class ManifestModel(_ManifestSchema):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)
    required: bool
    backend: Literal["onnxruntime", "pytorch"]
    providers: tuple[
        Literal["CPUExecutionProvider", "CUDAExecutionProvider"], ...
    ] = Field(min_length=1)
    expected: ExpectedOutput
    feature_version: FeatureVersion | None

    @field_validator("path", mode="before")
    @classmethod
    def convert_path(cls, value: Any) -> Any:
        return Path(value) if isinstance(value, str) else value

    @field_validator("providers", mode="before")
    @classmethod
    def convert_providers(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("feature_version", mode="before")
    @classmethod
    def convert_feature_version(cls, value: Any) -> Any:
        return FeatureVersion(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_recognizer_contract(self) -> Self:
        if self.name == "face_recognizer":
            if (
                self.feature_version is not FeatureVersion.RECOGNIZER_V1_1024
                or self.expected.output_dimension != FEATURE_DIMENSION
            ):
                raise ValueError(
                    "face_recognizer contract must be recognizer_v1_1024/1024"
                )
        elif self.feature_version is not None:
            raise ValueError("only face_recognizer may declare a feature version")
        return self

    @property
    def resolved_path(self) -> Path:
        return self.path


class Manifest(_ManifestSchema):
    manifest_version: Literal["2026-08-04-v1"]
    models: tuple[ManifestModel, ...] = Field(min_length=1)

    @field_validator("models", mode="before")
    @classmethod
    def convert_models(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_v1_roles(self) -> Self:
        names = [model.name for model in self.models]
        if len(names) != len(set(names)):
            raise ValueError("manifest model names must be unique")
        if set(names) != _EXPECTED_MODEL_NAMES:
            missing = sorted(_EXPECTED_MODEL_NAMES - set(names))
            extra = sorted(set(names) - _EXPECTED_MODEL_NAMES)
            raise ValueError(
                f"v1 manifest model roles differ: missing={missing}, extra={extra}"
            )
        for model in self.models:
            expected_required = model.name in _REQUIRED_MODEL_NAMES
            if model.required is not expected_required:
                raise ValueError(
                    f"v1 manifest required flag is invalid for {model.name}"
                )
        return self


@dataclass(frozen=True, slots=True)
class ModelContract:
    """Runtime evidence, normally produced by one dummy inference."""

    feature_version: str
    output_dimension: int


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedArtifact:
    """Exact model bytes whose size and digest matched a manifest entry."""

    entry: ManifestModel
    data: bytes = field(repr=False)
    sha256: str
    size: int

    def __repr__(self) -> str:
        return (
            "VerifiedArtifact("
            f"name={self.entry.name!r}, sha256={self.sha256!r}, size={self.size}"
            ")"
        )


@dataclass(frozen=True, slots=True)
class VerifiedModelEvidence:
    """Read-only evidence from one successfully loaded, verified model."""

    name: str
    version: str
    sha256: str
    size: int
    backend: str
    providers: tuple[str, ...]
    feature_version: str | None
    output_dimension: int | None


@dataclass(frozen=True, slots=True)
class LoadedModelHandle:
    """One model instance and the evidence captured from its same ready slot."""

    instance: object
    evidence: VerifiedModelEvidence


_DetailValue: TypeAlias = str | int | float | bool | None
_FrozenDetails: TypeAlias = tuple[tuple[str, _DetailValue], ...]


def _freeze_details(details: Mapping[str, Any] | None) -> _FrozenDetails:
    frozen: list[tuple[str, _DetailValue]] = []
    for key, value in (details or {}).items():
        if not isinstance(key, str) or not isinstance(
            value,
            (str, int, float, bool, type(None)),
        ):
            raise TypeError("model error details must contain scalar values")
        frozen.append((key, value))
    return tuple(sorted(frozen))


class ModelRegistryError(RuntimeError):
    def __init__(
        self,
        model_name: str,
        reason_code: ReasonCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.model_name = model_name
        self.reason_code = reason_code
        self.details = MappingProxyType(dict(_freeze_details(details)))


class ModelUnavailableError(ModelRegistryError):
    pass


class ModelIntegrityError(ModelUnavailableError):
    pass


class ModelContractError(ModelRegistryError):
    pass


class ModelLoadError(ModelRegistryError):
    pass


@dataclass(frozen=True, slots=True)
class _FailureData:
    exception_type: type[ModelRegistryError]
    model_name: str
    reason_code: ReasonCode
    message: str
    details: _FrozenDetails

    @classmethod
    def from_error(cls, error: ModelRegistryError) -> _FailureData:
        return cls(
            exception_type=type(error),
            model_name=error.model_name,
            reason_code=error.reason_code,
            message=str(error),
            details=_freeze_details(error.details),
        )

    def new_exception(self) -> ModelRegistryError:
        return self.exception_type(
            self.model_name,
            self.reason_code,
            self.message,
            details=dict(self.details),
        )


ModelFactory = Callable[[VerifiedArtifact], object]
ContractValidator = Callable[[object, VerifiedArtifact], ModelContract]
ProviderResolver = Callable[[object, VerifiedArtifact], tuple[str, ...]]


@dataclass(slots=True)
class _ModelSlot:
    condition: threading.Condition
    state: Literal["idle", "loading", "ready", "failed"] = "idle"
    owner_thread_id: int | None = None
    instance: object | None = None
    failure: _FailureData | None = None
    health: ModelHealth | None = None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def load_manifest(path: str | Path) -> Manifest:
    """Load a strict v1 manifest and resolve paths from its directory."""

    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(
        manifest_path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("model manifest root must be an object")
    models = payload.get("models")
    if isinstance(models, list):
        normalized_models: list[Any] = []
        for raw_model in models:
            if not isinstance(raw_model, dict):
                normalized_models.append(raw_model)
                continue
            normalized = dict(raw_model)
            raw_path = normalized.get("path")
            if isinstance(raw_path, str):
                model_path = Path(raw_path)
                if not model_path.is_absolute():
                    model_path = manifest_path.parent / model_path
                normalized["path"] = model_path.resolve()
            normalized_models.append(normalized)
        payload = dict(payload)
        payload["models"] = normalized_models
    return Manifest.model_validate(payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_error(entry: ManifestModel) -> ModelRegistryError | None:
    """Point-in-time artifact check used only by the manifest health CLI."""

    path = entry.resolved_path
    try:
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
    except OSError:
        return ModelUnavailableError(
            entry.name,
            ReasonCode.MODEL_UNAVAILABLE,
            "model artifact is unavailable",
            details={"actual_sha256": None},
        )
    if actual_size != entry.size or actual_hash != entry.sha256:
        return ModelIntegrityError(
            entry.name,
            ReasonCode.MODEL_UNAVAILABLE,
            "model artifact failed size or SHA-256 verification",
            details={
                "actual_sha256": actual_hash,
                "actual_size": actual_size,
                "expected_size": entry.size,
            },
        )
    return None


def _read_verified_artifact(entry: ManifestModel) -> VerifiedArtifact:
    try:
        data = entry.resolved_path.read_bytes()
    except OSError as error:
        raise ModelUnavailableError(
            entry.name,
            ReasonCode.MODEL_UNAVAILABLE,
            "model artifact is unavailable",
            details={"actual_sha256": None},
        ) from None
    actual_hash = hashlib.sha256(data).hexdigest()
    actual_size = len(data)
    if actual_size != entry.size or actual_hash != entry.sha256:
        raise ModelIntegrityError(
            entry.name,
            ReasonCode.MODEL_UNAVAILABLE,
            "model artifact failed size or SHA-256 verification",
            details={
                "actual_sha256": actual_hash,
                "actual_size": actual_size,
                "expected_size": entry.size,
            },
        )
    return VerifiedArtifact(
        entry=entry,
        data=data,
        sha256=actual_hash,
        size=actual_size,
    )


def _health_from_failure(
    entry: ManifestModel,
    failure: _FailureData,
) -> ModelHealth:
    details = dict(failure.details)
    actual_hash = details.get("actual_sha256")
    unavailable = issubclass(failure.exception_type, ModelUnavailableError)
    return ModelHealth(
        name=entry.name,
        required=entry.required,
        state=ModelState.UNAVAILABLE if unavailable else ModelState.ERROR,
        version=entry.version,
        providers=entry.providers,
        expected_sha256=entry.sha256,
        actual_sha256=actual_hash if isinstance(actual_hash, str) else None,
        feature_version=entry.feature_version,
        output_dimension=entry.expected.output_dimension,
        error_code=failure.reason_code,
        error_message=failure.message,
    )


def _ready_health(
    artifact: VerifiedArtifact,
    contract: ModelContract | None,
    providers: tuple[str, ...],
) -> ModelHealth:
    entry = artifact.entry
    return ModelHealth(
        name=entry.name,
        required=entry.required,
        state=ModelState.READY,
        version=entry.version,
        providers=providers,
        expected_sha256=entry.sha256,
        actual_sha256=artifact.sha256,
        feature_version=entry.feature_version,
        output_dimension=(
            contract.output_dimension
            if contract is not None
            else entry.expected.output_dimension
        ),
    )


def _aggregate_health(models: tuple[ModelHealth, ...]) -> HealthStatus:
    if any(
        model.required and model.state is not ModelState.READY for model in models
    ):
        status = HealthLevel.UNHEALTHY
    elif any(
        not model.required and model.state is not ModelState.READY for model in models
    ):
        status = HealthLevel.DEGRADED
    else:
        status = HealthLevel.HEALTHY
    return HealthStatus(status=status, models=models)


def verify_manifest_artifacts(path: str | Path) -> HealthStatus:
    """Verify manifest artifacts without constructing model sessions."""

    manifest = load_manifest(path)
    health: list[ModelHealth] = []
    for entry in manifest.models:
        error = _artifact_error(entry)
        if error is not None:
            health.append(_health_from_failure(entry, _FailureData.from_error(error)))
            continue
        health.append(
            ModelHealth(
                name=entry.name,
                required=entry.required,
                state=ModelState.READY,
                version=entry.version,
                providers=entry.providers,
                expected_sha256=entry.sha256,
                actual_sha256=entry.sha256,
                feature_version=entry.feature_version,
                output_dimension=entry.expected.output_dimension,
            )
        )
    return _aggregate_health(tuple(health))


class ModelRegistry:
    """Load each model once without holding the registry lock during I/O.

    Factories may synchronously request a different model. A same-thread cycle
    is rejected immediately. Cross-thread cyclic factory dependencies are
    forbidden; waiters still have a finite timeout so such a bug cannot hang a
    process forever.
    """

    def __init__(
        self,
        manifest_path: str | Path,
        factories: Mapping[str, ModelFactory],
        *,
        contract_validators: Mapping[str, ContractValidator] | None = None,
        provider_resolvers: Mapping[str, ProviderResolver] | None = None,
        wait_timeout_seconds: float = 60.0,
        _manifest_snapshot: Manifest | None = None,
    ) -> None:
        if (
            isinstance(wait_timeout_seconds, bool)
            or not isinstance(wait_timeout_seconds, (int, float))
            or not math.isfinite(float(wait_timeout_seconds))
            or wait_timeout_seconds <= 0
        ):
            raise ValueError("wait_timeout_seconds must be a finite positive number")
        self._manifest_path = Path(manifest_path).expanduser().resolve()
        self._manifest_snapshot = (
            _manifest_snapshot
            if _manifest_snapshot is not None
            else load_manifest(self._manifest_path)
        )
        self.manifest = self._manifest_snapshot
        self._entries = {entry.name: entry for entry in self.manifest.models}
        self._factories = MappingProxyType(dict(factories))
        self._contract_validators = MappingProxyType(
            dict(contract_validators or {})
        )
        self._provider_resolvers = MappingProxyType(dict(provider_resolvers or {}))
        self._wait_timeout_seconds = float(wait_timeout_seconds)
        self._lock = threading.RLock()
        self._slots = {
            name: _ModelSlot(condition=threading.Condition(self._lock))
            for name in self._entries
        }

    def spawn_isolated(self, names: Iterable[str]) -> ModelRegistry:
        """Create and preload a registry whose instances are not caller-owned.

        The immutable manifest/factory snapshots are reused, while every
        requested model is constructed again from freshly verified bytes.
        Identity and evidence checks prevent a singleton factory from silently
        reintroducing a shared mutable execution object.
        """

        requested = tuple(names)
        if not requested:
            raise ValueError("isolated registry requires at least one model")
        if any(not isinstance(name, str) or not name for name in requested):
            raise TypeError("isolated registry model names must be non-empty strings")
        if len(requested) != len(set(requested)):
            raise ValueError("isolated registry model names must be unique")
        unknown = tuple(name for name in requested if name not in self._entries)
        if unknown:
            raise KeyError(f"unknown model: {unknown[0]}")

        source_handles = {
            name: self.get_with_evidence(name) for name in requested
        }
        isolated = ModelRegistry(
            self._manifest_path,
            self._factories,
            contract_validators=self._contract_validators,
            provider_resolvers=self._provider_resolvers,
            wait_timeout_seconds=self._wait_timeout_seconds,
            _manifest_snapshot=self._manifest_snapshot,
        )
        for name in requested:
            owned = isolated.get_with_evidence(name)
            source = source_handles[name]
            if owned.instance is source.instance:
                raise ModelLoadError(
                    name,
                    ReasonCode.MODEL_UNAVAILABLE,
                    "isolated model factory reused a caller-owned instance",
                )
            if owned.evidence != source.evidence:
                raise ModelIntegrityError(
                    name,
                    ReasonCode.MODEL_UNAVAILABLE,
                    "isolated model evidence differs from the source snapshot",
                )
        return isolated

    def get(self, name: str) -> object:
        """Return one verified instance, constructing it at most once."""

        try:
            entry = self._entries[name]
        except KeyError as error:
            raise KeyError(f"unknown model: {name}") from error
        thread_id = threading.get_ident()

        with self._lock:
            slot = self._slots[name]
            if slot.state == "ready":
                return slot.instance
            if slot.state == "failed":
                assert slot.failure is not None
                raise slot.failure.new_exception()
            if slot.state == "loading":
                if slot.owner_thread_id == thread_id:
                    raise ModelLoadError(
                        name,
                        ReasonCode.MODEL_UNAVAILABLE,
                        "circular model dependency detected",
                        details={"dependency": name},
                    )
                deadline = time.monotonic() + self._wait_timeout_seconds
                while slot.state == "loading":
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not slot.condition.wait(timeout=remaining):
                        raise ModelLoadError(
                            name,
                            ReasonCode.ALGORITHM_TIMEOUT,
                            "timed out waiting for model loading",
                            details={"timeout_ms": int(self._wait_timeout_seconds * 1000)},
                        )
                if slot.state == "ready":
                    return slot.instance
                assert slot.failure is not None
                raise slot.failure.new_exception()

            slot.state = "loading"
            slot.owner_thread_id = thread_id

        instance: object | None = None
        artifact: VerifiedArtifact | None = None
        contract: ModelContract | None = None
        providers: tuple[str, ...] = ()
        failure: _FailureData | None = None
        try:
            artifact = _read_verified_artifact(entry)
            if (
                entry.feature_version is not None
                and name not in self._contract_validators
            ):
                raise ModelContractError(
                    name,
                    ReasonCode.FEATURE_VERSION_MISMATCH,
                    "recognizer runtime contract validator is required",
                    details={"actual_sha256": artifact.sha256},
                )
            factory = self._factories.get(name)
            if factory is None:
                raise ModelUnavailableError(
                    name,
                    ReasonCode.MODEL_UNAVAILABLE,
                    "no model factory is registered",
                    details={"actual_sha256": artifact.sha256},
                )
            instance = factory(artifact)
            if instance is None:
                raise ModelLoadError(
                    name,
                    ReasonCode.INFERENCE_ERROR,
                    "model factory returned no instance",
                    details={"actual_sha256": artifact.sha256},
                )
            contract = self._validate_runtime_contract(instance, artifact)
            providers = self._snapshot_runtime_providers(instance, artifact)
        except ModelRegistryError as error:
            if error.model_name == name:
                failure = _FailureData.from_error(error)
            else:
                failure = _FailureData.from_error(
                    ModelLoadError(
                        name,
                        error.reason_code,
                        f"model dependency failed: {error.model_name}",
                        details={"dependency": error.model_name},
                    )
                )
        except Exception as error:
            failure = _FailureData.from_error(
                ModelLoadError(
                    name,
                    ReasonCode.INFERENCE_ERROR,
                    f"model factory or contract validator failed: {type(error).__name__}",
                    details={
                        "actual_sha256": artifact.sha256 if artifact else None,
                    },
                )
            )

        with self._lock:
            slot = self._slots[name]
            slot.owner_thread_id = None
            if failure is not None:
                slot.state = "failed"
                slot.failure = failure
                slot.instance = None
                slot.health = _health_from_failure(entry, failure)
            else:
                assert artifact is not None and instance is not None
                slot.state = "ready"
                slot.failure = None
                slot.instance = instance
                slot.health = _ready_health(artifact, contract, providers)
            slot.condition.notify_all()

        if failure is not None:
            raise failure.new_exception()
        return instance

    def _snapshot_runtime_providers(
        self,
        instance: object,
        artifact: VerifiedArtifact,
    ) -> tuple[str, ...]:
        resolver = self._provider_resolvers.get(artifact.entry.name)
        if resolver is not None:
            raw_providers: object = resolver(instance, artifact)
        else:
            getter = getattr(instance, "get_providers", None)
            if not callable(getter):
                # Unknown is intentionally represented as empty. Requested
                # manifest providers are not evidence of what became active.
                return ()
            raw_providers = getter()
        if isinstance(raw_providers, str):
            raise TypeError("runtime provider snapshot must be a sequence")
        try:
            providers = tuple(raw_providers)  # type: ignore[arg-type]
        except TypeError as error:
            raise TypeError("runtime provider snapshot must be a sequence") from error
        if any(not isinstance(provider, str) or not provider for provider in providers):
            raise ValueError("runtime provider names must be non-empty strings")
        if len(providers) != len(set(providers)):
            raise ValueError("runtime provider snapshot must not contain duplicates")
        return providers

    def verified_evidence(self, name: str) -> VerifiedModelEvidence:
        """Return immutable evidence without loading or otherwise changing state."""

        try:
            entry = self._entries[name]
        except KeyError as error:
            raise KeyError(f"unknown model: {name}") from error
        with self._lock:
            slot = self._slots[name]
            return self._ready_evidence(name, entry, slot)

    def get_with_evidence(self, name: str) -> LoadedModelHandle:
        """Atomically snapshot a ready instance and its verified evidence."""

        if name not in self._entries:
            error = KeyError(name)
            raise KeyError(f"unknown model: {name}") from error
        deadline = time.monotonic() + self._wait_timeout_seconds
        while time.monotonic() < deadline:
            # The return value is deliberately ignored. A concurrent reset may
            # replace the ready slot before this method reacquires the lock.
            self.get(name)
            with self._lock:
                if time.monotonic() >= deadline:
                    break
                slot = self._slots[name]
                if slot.state != "ready" or slot.instance is None:
                    continue
                entry = self._entries[name]
                evidence = self._ready_evidence(name, entry, slot)
                return LoadedModelHandle(instance=slot.instance, evidence=evidence)
        raise ModelLoadError(
            name,
            ReasonCode.ALGORITHM_TIMEOUT,
            "timed out acquiring an atomic model evidence snapshot",
            details={"timeout_ms": int(self._wait_timeout_seconds * 1000)},
        )

    @staticmethod
    def _ready_evidence(
        name: str,
        entry: ManifestModel,
        slot: _ModelSlot,
    ) -> VerifiedModelEvidence:
        if slot.state != "ready" or slot.health is None:
            raise ModelUnavailableError(
                name,
                ReasonCode.MODEL_UNAVAILABLE,
                "verified model evidence is unavailable before successful load",
            )
        health = slot.health
        if health.actual_sha256 is None:
            raise ModelUnavailableError(
                name,
                ReasonCode.MODEL_UNAVAILABLE,
                "verified model digest is unavailable",
            )
        return VerifiedModelEvidence(
            name=entry.name,
            version=entry.version,
            sha256=health.actual_sha256,
            size=entry.size,
            backend=entry.backend,
            providers=health.providers,
            feature_version=(
                health.feature_version.value
                if health.feature_version is not None
                else None
            ),
            output_dimension=health.output_dimension,
        )

    def _validate_runtime_contract(
        self,
        instance: object,
        artifact: VerifiedArtifact,
    ) -> ModelContract | None:
        entry = artifact.entry
        if entry.feature_version is None:
            return None
        validator = self._contract_validators.get(entry.name)
        if validator is None:
            raise ModelContractError(
                entry.name,
                ReasonCode.FEATURE_VERSION_MISMATCH,
                "recognizer runtime contract validator is required",
                details={"actual_sha256": artifact.sha256},
            )
        contract = validator(instance, artifact)
        expected_version = entry.feature_version.value
        if (
            contract.feature_version != expected_version
            or contract.output_dimension != entry.expected.output_dimension
            or contract.output_dimension != FEATURE_DIMENSION
        ):
            raise ModelContractError(
                entry.name,
                ReasonCode.FEATURE_VERSION_MISMATCH,
                "recognizer runtime contract does not match recognizer_v1_1024",
                details={
                    "actual_feature_version": contract.feature_version,
                    "actual_output_dimension": contract.output_dimension,
                    "expected_feature_version": expected_version,
                    "expected_output_dimension": FEATURE_DIMENSION,
                    "actual_sha256": artifact.sha256,
                },
            )
        return contract

    def reset(self, name: str) -> None:
        """Clear one ready/failed cache entry so an operator can retry it."""

        if name not in self._entries:
            raise KeyError(f"unknown model: {name}")
        with self._lock:
            slot = self._slots[name]
            if slot.state == "loading":
                raise ModelLoadError(
                    name,
                    ReasonCode.MODEL_UNAVAILABLE,
                    "cannot reset a model while it is loading",
                )
            slot.state = "idle"
            slot.owner_thread_id = None
            slot.instance = None
            slot.failure = None
            slot.health = None

    def health(self) -> HealthStatus:
        """Load every role once and aggregate required versus optional failures."""

        transient_failures: dict[str, _FailureData] = {}
        for entry in self.manifest.models:
            with self._lock:
                already_checked = self._slots[entry.name].health is not None
            if already_checked:
                continue
            try:
                self.get(entry.name)
            except ModelRegistryError as error:
                transient_failures[entry.name] = _FailureData.from_error(error)

        models: list[ModelHealth] = []
        with self._lock:
            for entry in self.manifest.models:
                slot = self._slots[entry.name]
                if slot.health is not None:
                    models.append(slot.health)
                    continue
                failure = transient_failures.get(entry.name) or slot.failure
                if failure is None and slot.state == "loading":
                    failure = _FailureData.from_error(
                        ModelLoadError(
                            entry.name,
                            ReasonCode.ALGORITHM_TIMEOUT,
                            "model loading is still in progress",
                            details={
                                "timeout_ms": int(
                                    self._wait_timeout_seconds * 1000
                                )
                            },
                        )
                    )
                if failure is None:
                    failure = _FailureData.from_error(
                        ModelUnavailableError(
                            entry.name,
                            ReasonCode.MODEL_UNAVAILABLE,
                            "model health is not yet available",
                        )
                    )
                models.append(_health_from_failure(entry, failure))
        return _aggregate_health(tuple(models))
