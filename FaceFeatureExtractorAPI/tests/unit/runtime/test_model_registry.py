from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_registry import (
    LoadedModelHandle,
    ModelContract,
    ModelContractError,
    ModelIntegrityError,
    ModelLoadError,
    ModelRegistry,
    ModelRegistryError,
    ModelUnavailableError,
    VerifiedModelEvidence,
    load_manifest,
    verify_manifest_artifacts,
)
from face_algorithm.schemas import HealthLevel, ModelState, ReasonCode


MANIFEST_VERSION = "2026-08-04-v1"
CORE_MODEL_NAMES = (
    "face_detector",
    "face_landmark_primary",
    "face_landmark_secondary",
    "face_recognizer",
    "rotation_classifier",
    "pose_var",
    "pose_conv",
)
OPTIONAL_MODEL_NAME = "portrait_matting"
ALL_MODEL_NAMES = (*CORE_MODEL_NAMES, OPTIONAL_MODEL_NAME)


def _model_payload(name: str, data: bytes) -> dict:
    recognizer = name == "face_recognizer"
    return {
        "name": name,
        "version": "test-v1",
        "path": f"models/{name}.bin",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "required": name in CORE_MODEL_NAMES,
        "backend": "pytorch" if name == OPTIONAL_MODEL_NAME else "onnxruntime",
        "providers": ["CPUExecutionProvider"],
        "expected": {"output_dimension": 1024 if recognizer else None},
        "feature_version": "recognizer_v1_1024" if recognizer else None,
    }


def _write_manifest(
    root: Path,
    *,
    missing: set[str] | None = None,
    hash_overrides: dict[str, str] | None = None,
) -> tuple[Path, dict[str, bytes]]:
    missing = missing or set()
    hash_overrides = hash_overrides or {}
    model_dir = root / "models"
    model_dir.mkdir(parents=True)
    artifacts = {
        name: f"deterministic-model:{name}".encode("ascii")
        for name in ALL_MODEL_NAMES
    }
    models: list[dict] = []
    for name in ALL_MODEL_NAMES:
        data = artifacts[name]
        if name not in missing:
            (model_dir / f"{name}.bin").write_bytes(data)
        payload = _model_payload(name, data)
        if name in hash_overrides:
            payload["sha256"] = hash_overrides[name]
        models.append(payload)
    manifest_path = root / "models.manifest.json"
    manifest_path.write_text(
        json.dumps({"manifest_version": MANIFEST_VERSION, "models": models}),
        encoding="utf-8",
    )
    return manifest_path, artifacts


def _validator(
    *,
    dimension: int = 1024,
    version: str = "recognizer_v1_1024",
):
    def validate(_model, _artifact) -> ModelContract:
        return ModelContract(feature_version=version, output_dimension=dimension)

    return validate


def _factories(names=ALL_MODEL_NAMES) -> dict[str, object]:
    def build(name: str):
        def factory(artifact):
            assert artifact.entry.name == name
            return SimpleNamespace(
                name=name,
                payload=artifact.data,
                get_providers=lambda: ["CPUExecutionProvider"],
            )

        return factory

    return {name: build(name) for name in names}


def _validators() -> dict[str, object]:
    return {"face_recognizer": _validator()}


def test_spawn_isolated_uses_frozen_manifest_and_independent_instances(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    registry = ModelRegistry(
        manifest_path,
        _factories(),
        contract_validators=_validators(),
    )
    shared = registry.get("face_recognizer")

    manifest_path.write_text("not valid json", encoding="utf-8")
    isolated = registry.spawn_isolated(("face_recognizer",))
    owned = isolated.get("face_recognizer")

    assert isolated is not registry
    assert owned is not shared
    assert owned.payload == shared.payload
    shared.payload = b"caller-mutated"
    assert owned.payload != shared.payload


def test_spawn_isolated_rejects_a_factory_that_reuses_the_source_instance(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    singleton = SimpleNamespace(
        get_providers=lambda: ["CPUExecutionProvider"],
    )
    registry = ModelRegistry(
        manifest_path,
        {"face_recognizer": lambda _artifact: singleton},
        contract_validators=_validators(),
    )

    with pytest.raises(ModelLoadError, match="caller-owned instance"):
        registry.spawn_isolated(("face_recognizer",))


def test_verified_evidence_is_read_only_and_available_only_after_load(
    tmp_path: Path,
) -> None:
    manifest_path, artifacts = _write_manifest(tmp_path)
    registry = ModelRegistry(
        manifest_path,
        _factories(),
        contract_validators=_validators(),
    )

    with pytest.raises(ModelUnavailableError):
        registry.verified_evidence("face_recognizer")

    registry.get("face_recognizer")
    evidence = registry.verified_evidence("face_recognizer")

    assert isinstance(evidence, VerifiedModelEvidence)
    assert evidence.name == "face_recognizer"
    assert evidence.version == "test-v1"
    assert evidence.sha256 == hashlib.sha256(artifacts["face_recognizer"]).hexdigest()
    assert evidence.size == len(artifacts["face_recognizer"])
    assert evidence.backend == "onnxruntime"
    assert evidence.providers == ("CPUExecutionProvider",)
    assert evidence.feature_version == "recognizer_v1_1024"
    assert evidence.output_dimension == 1024
    with pytest.raises((AttributeError, TypeError)):
        evidence.sha256 = "0" * 64

    registry.reset("face_recognizer")
    with pytest.raises(ModelUnavailableError):
        registry.verified_evidence("face_recognizer")


def test_get_with_evidence_retries_if_reset_reloads_between_get_and_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    model_path = tmp_path / "models" / "face_detector.bin"

    def factory(artifact):
        provider = (
            "CPUExecutionProvider"
            if artifact.data.startswith(b"deterministic")
            else "CUDAExecutionProvider"
        )
        return SimpleNamespace(
            payload=artifact.data,
            get_providers=lambda: [provider],
        )

    registry = ModelRegistry(manifest_path, {"face_detector": factory})
    old_instance = registry.get("face_detector")
    original_get = registry.get
    raced = False
    reloaded: list[object] = []

    def get_with_forced_reset(name: str):
        nonlocal raced
        stale_return_value = original_get(name)
        if not raced:
            raced = True
            replacement = b"replacement-model:face_detector:v2"
            model_path.write_bytes(replacement)
            old_entry = registry._entries[name]
            registry._entries[name] = old_entry.model_copy(
                update={
                    "version": "test-v2",
                    "sha256": hashlib.sha256(replacement).hexdigest(),
                    "size": len(replacement),
                }
            )
            registry.reset(name)
            reloaded.append(original_get(name))
        return stale_return_value

    monkeypatch.setattr(registry, "get", get_with_forced_reset)

    handle = registry.get_with_evidence("face_detector")

    assert isinstance(handle, LoadedModelHandle)
    assert handle.instance is reloaded[0]
    assert handle.instance is not old_instance
    assert handle.instance.payload == b"replacement-model:face_detector:v2"
    assert handle.evidence.version == "test-v2"
    assert handle.evidence.sha256 == hashlib.sha256(
        b"replacement-model:face_detector:v2"
    ).hexdigest()
    assert handle.evidence.providers == ("CUDAExecutionProvider",)
    with pytest.raises((AttributeError, TypeError)):
        handle.evidence = registry.verified_evidence("face_detector")


def test_get_with_evidence_bounds_repeated_reset_retries(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    registry = ModelRegistry(
        manifest_path,
        {"face_detector": lambda _artifact: object()},
        wait_timeout_seconds=0.01,
    )
    original_get = registry.get

    def always_reset(name: str):
        instance = original_get(name)
        registry.reset(name)
        return instance

    registry.get = always_reset

    started = time.monotonic()
    with pytest.raises(ModelLoadError) as caught:
        registry.get_with_evidence("face_detector")

    assert time.monotonic() - started < 0.5
    assert caught.value.reason_code is ReasonCode.ALGORITHM_TIMEOUT


def test_get_with_evidence_does_not_return_after_total_deadline(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    registry = ModelRegistry(
        manifest_path,
        {"face_detector": lambda _artifact: object()},
        wait_timeout_seconds=0.01,
    )
    original_get = registry.get

    def slow_get(name: str):
        instance = original_get(name)
        time.sleep(0.02)
        return instance

    registry.get = slow_get

    with pytest.raises(ModelLoadError) as caught:
        registry.get_with_evidence("face_detector")

    assert caught.value.reason_code is ReasonCode.ALGORITHM_TIMEOUT


def test_verified_evidence_uses_actual_session_providers_not_requested_ones(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    detector = next(
        item for item in payload["models"] if item["name"] == "face_detector"
    )
    detector["providers"] = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    class CpuOnlySession:
        def get_providers(self) -> list[str]:
            return ["CPUExecutionProvider"]

    registry = ModelRegistry(
        manifest_path,
        {"face_detector": lambda _artifact: CpuOnlySession()},
    )
    registry.get("face_detector")

    evidence = registry.verified_evidence("face_detector")
    health = next(
        model for model in registry.health().models if model.name == "face_detector"
    )
    assert evidence.providers == ("CPUExecutionProvider",)
    assert health.providers == ("CPUExecutionProvider",)


def test_non_session_factory_never_reports_requested_providers_as_actual(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    registry = ModelRegistry(
        manifest_path,
        {"face_detector": lambda _artifact: object()},
    )

    registry.get("face_detector")

    assert registry.verified_evidence("face_detector").providers == ()


def test_non_session_factory_can_inject_an_explicit_provider_snapshot(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    registry = ModelRegistry(
        manifest_path,
        {"face_detector": lambda _artifact: object()},
        provider_resolvers={
            "face_detector": lambda _instance, _artifact: (
                "CPUExecutionProvider",
            )
        },
    )

    registry.get("face_detector")

    assert registry.verified_evidence("face_detector").providers == (
        "CPUExecutionProvider",
    )


def test_registry_caches_one_factory_result_for_repeated_gets(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    calls = 0

    def factory(_artifact):
        nonlocal calls
        calls += 1
        return object()

    registry = ModelRegistry(
        manifest_path,
        {"face_recognizer": factory},
        contract_validators=_validators(),
    )

    first = registry.get("face_recognizer")
    second = registry.get("face_recognizer")
    assert first is second
    assert calls == 1


def test_same_model_concurrency_constructs_one_session(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    calls = 0
    calls_lock = threading.Lock()

    def factory(_artifact):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.02)
        return object()

    registry = ModelRegistry(
        manifest_path,
        {"face_recognizer": factory},
        contract_validators=_validators(),
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        instances = list(
            executor.map(lambda _unused: registry.get("face_recognizer"), range(16))
        )

    assert all(instance is instances[0] for instance in instances)
    assert calls == 1


def test_different_models_can_enter_blocking_factories_concurrently(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    both_entered = threading.Barrier(2, timeout=0.5)

    def factory(artifact):
        both_entered.wait()
        return artifact.entry.name

    registry = ModelRegistry(
        manifest_path,
        {"face_detector": factory, "rotation_classifier": factory},
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        detector = executor.submit(registry.get, "face_detector")
        rotation = executor.submit(registry.get, "rotation_classifier")
        assert {detector.result(timeout=1), rotation.result(timeout=1)} == {
            "face_detector",
            "rotation_classifier",
        }


def test_factory_can_load_a_different_model_in_the_same_thread(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    holder: dict[str, ModelRegistry] = {}

    def detector_factory(_artifact):
        return ("detector", holder["registry"].get("rotation_classifier"))

    registry = ModelRegistry(
        manifest_path,
        {
            "face_detector": detector_factory,
            "rotation_classifier": lambda _artifact: "rotation",
        },
    )
    holder["registry"] = registry

    assert registry.get("face_detector") == ("detector", "rotation")


def test_factory_thread_can_join_loading_of_a_different_model(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    holder: dict[str, ModelRegistry] = {}
    child_threads: list[threading.Thread] = []
    child_results: list[object] = []

    def detector_factory(_artifact):
        child = threading.Thread(
            target=lambda: child_results.append(
                holder["registry"].get("rotation_classifier")
            )
        )
        child_threads.append(child)
        child.start()
        child.join(timeout=0.5)
        if child.is_alive():
            raise AssertionError("different-model load was blocked by a global lock")
        return "detector"

    registry = ModelRegistry(
        manifest_path,
        {
            "face_detector": detector_factory,
            "rotation_classifier": lambda _artifact: "rotation",
        },
    )
    holder["registry"] = registry
    try:
        assert registry.get("face_detector") == "detector"
    finally:
        for child in child_threads:
            child.join(timeout=1)
    assert child_results == ["rotation"]


def test_same_thread_dependency_cycle_fails_fast(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    holder: dict[str, ModelRegistry] = {}
    calls = {"face_detector": 0, "rotation_classifier": 0}

    def detector_factory(_artifact):
        calls["face_detector"] += 1
        return holder["registry"].get("rotation_classifier")

    def rotation_factory(_artifact):
        calls["rotation_classifier"] += 1
        return holder["registry"].get("face_detector")

    registry = ModelRegistry(
        manifest_path,
        {
            "face_detector": detector_factory,
            "rotation_classifier": rotation_factory,
        },
    )
    holder["registry"] = registry

    started = time.monotonic()
    with pytest.raises(ModelRegistryError) as caught:
        registry.get("face_detector")
    assert time.monotonic() - started < 0.5
    assert caught.value.reason_code is ReasonCode.MODEL_UNAVAILABLE
    assert calls == {"face_detector": 1, "rotation_classifier": 1}


def test_waiting_for_a_loading_model_has_a_bounded_timeout(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def factory(_artifact):
        entered.set()
        assert release.wait(timeout=1)
        return "detector"

    registry = ModelRegistry(
        manifest_path,
        {"face_detector": factory},
        wait_timeout_seconds=0.05,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        owner = executor.submit(registry.get, "face_detector")
        assert entered.wait(timeout=0.5)
        started = time.monotonic()
        with pytest.raises(ModelLoadError) as caught:
            registry.get("face_detector")
        assert time.monotonic() - started < 0.5
        assert caught.value.reason_code is ReasonCode.ALGORITHM_TIMEOUT
        release.set()
        assert owner.result(timeout=1) == "detector"


def test_health_reports_in_flight_timeout_ephemerally_and_recovers(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def slow_detector_factory(_artifact):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return "detector"

    factories = _factories()
    factories["face_detector"] = slow_detector_factory
    registry = ModelRegistry(
        manifest_path,
        factories,
        contract_validators=_validators(),
        wait_timeout_seconds=0.05,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        owner = executor.submit(registry.get, "face_detector")
        assert entered.wait(timeout=0.5)
        try:
            in_flight = registry.health()
        finally:
            release.set()
        assert owner.result(timeout=1) == "detector"

    assert [model.name for model in in_flight.models] == list(ALL_MODEL_NAMES)
    loading = in_flight.models[0]
    assert loading.state is not ModelState.READY
    assert loading.error_code is ReasonCode.ALGORITHM_TIMEOUT
    assert in_flight.status is HealthLevel.UNHEALTHY
    assert calls == 1

    recovered = registry.health()
    assert [model.name for model in recovered.models] == list(ALL_MODEL_NAMES)
    assert all(model.state is ModelState.READY for model in recovered.models)
    assert recovered.status is HealthLevel.HEALTHY
    assert calls == 1


def test_failure_cache_rebuilds_exceptions_and_reset_allows_retry(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    calls = 0

    def factory(_artifact):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic factory failure")
        return "recovered"

    registry = ModelRegistry(manifest_path, {"face_detector": factory})
    caught: list[ModelLoadError] = []
    for _ in range(2):
        with pytest.raises(ModelLoadError) as failure:
            registry.get("face_detector")
        caught.append(failure.value)

    assert calls == 1
    assert caught[0] is not caught[1]
    assert caught[0].__cause__ is None and caught[1].__cause__ is None
    assert len(traceback.extract_tb(caught[0].__traceback__)) == len(
        traceback.extract_tb(caught[1].__traceback__)
    )
    with pytest.raises(TypeError):
        caught[0].details["mutated"] = True

    registry.reset("face_detector")
    assert registry.get("face_detector") == "recovered"
    assert calls == 2


def test_none_factory_result_becomes_a_cached_structured_failure(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    calls = 0

    def factory(_artifact):
        nonlocal calls
        calls += 1
        return None

    registry = ModelRegistry(manifest_path, {"face_detector": factory})

    with pytest.raises(ModelLoadError) as first:
        registry.get("face_detector")
    with pytest.raises(ModelLoadError) as second:
        registry.get("face_detector")
    assert first.value.reason_code is ReasonCode.INFERENCE_ERROR
    assert first.value is not second.value
    assert calls == 1


def test_reset_rejects_a_model_that_is_loading(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def factory(_artifact):
        entered.set()
        assert release.wait(timeout=1)
        return object()

    registry = ModelRegistry(manifest_path, {"face_detector": factory})
    with ThreadPoolExecutor(max_workers=1) as executor:
        loading = executor.submit(registry.get, "face_detector")
        assert entered.wait(timeout=0.5)
        with pytest.raises(ModelRegistryError) as caught:
            registry.reset("face_detector")
        assert caught.value.reason_code is ReasonCode.MODEL_UNAVAILABLE
        release.set()
        loading.result(timeout=1)


def test_verified_bytes_survive_source_replacement_before_factory_use(
    tmp_path: Path,
) -> None:
    manifest_path, artifacts = _write_manifest(tmp_path)
    source = tmp_path / "models" / "face_detector.bin"
    observed: list[bytes] = []

    def factory(artifact):
        source.write_bytes(b"replaced-after-verification")
        observed.append(artifact.data)
        assert "deterministic-model" not in repr(artifact)
        return "loaded"

    registry = ModelRegistry(manifest_path, {"face_detector": factory})

    assert registry.get("face_detector") == "loaded"
    assert observed == [artifacts["face_detector"]]


def test_hash_mismatch_is_unavailable_and_factory_is_not_called(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(
        tmp_path,
        hash_overrides={"face_recognizer": "f" * 64},
    )
    calls = 0

    def factory(_artifact):
        nonlocal calls
        calls += 1
        return object()

    registry = ModelRegistry(
        manifest_path,
        {"face_recognizer": factory},
        contract_validators=_validators(),
    )
    with pytest.raises(ModelIntegrityError) as caught:
        registry.get("face_recognizer")

    assert caught.value.reason_code is ReasonCode.MODEL_UNAVAILABLE
    by_name = {model.name: model for model in registry.health().models}
    assert by_name["face_recognizer"].state is ModelState.UNAVAILABLE
    assert by_name["face_recognizer"].actual_sha256 != (
        by_name["face_recognizer"].expected_sha256
    )
    assert calls == 0


def test_missing_required_model_is_unhealthy(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path, missing={"face_detector"})
    registry = ModelRegistry(
        manifest_path,
        _factories(),
        contract_validators=_validators(),
    )

    health = registry.health()
    by_name = {model.name: model for model in health.models}
    assert health.status is HealthLevel.UNHEALTHY
    assert by_name["face_detector"].state is ModelState.UNAVAILABLE
    assert by_name["face_detector"].error_code is ReasonCode.MODEL_UNAVAILABLE


def test_missing_optional_modnet_is_degraded_without_blocking_face_core(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path, missing={OPTIONAL_MODEL_NAME})
    registry = ModelRegistry(
        manifest_path,
        _factories(),
        contract_validators=_validators(),
    )
    first = registry.health()
    second = registry.health()

    assert first.status is HealthLevel.DEGRADED
    by_name = {model.name: model for model in first.models}
    assert all(by_name[name].state is ModelState.READY for name in CORE_MODEL_NAMES)
    assert by_name[OPTIONAL_MODEL_NAME].state is ModelState.UNAVAILABLE
    assert second == first


def test_recognizer_512_contract_is_rejected_with_structured_reason(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    registry = ModelRegistry(
        manifest_path,
        {"face_recognizer": lambda _artifact: object()},
        contract_validators={"face_recognizer": _validator(dimension=512)},
    )

    with pytest.raises(ModelContractError) as caught:
        registry.get("face_recognizer")
    assert caught.value.reason_code is ReasonCode.FEATURE_VERSION_MISMATCH
    assert caught.value.details["actual_output_dimension"] == 512


def test_recognizer_attributes_are_never_trusted_without_contract_validator(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    factory_calls = 0

    def factory(_artifact):
        nonlocal factory_calls
        factory_calls += 1
        return SimpleNamespace(
            feature_dim=1024,
            feature_version="recognizer_v1_1024",
        )

    registry = ModelRegistry(manifest_path, {"face_recognizer": factory})

    with pytest.raises(ModelContractError) as caught:
        registry.get("face_recognizer")
    assert caught.value.reason_code is ReasonCode.FEATURE_VERSION_MISMATCH
    assert factory_calls == 0


def test_recognizer_validator_runs_once_across_get_and_health(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    validator_calls = 0

    def validator(_model, artifact):
        nonlocal validator_calls
        assert artifact.entry.name == "face_recognizer"
        validator_calls += 1
        return ModelContract(
            feature_version="recognizer_v1_1024",
            output_dimension=1024,
        )

    registry = ModelRegistry(
        manifest_path,
        _factories(),
        contract_validators={"face_recognizer": validator},
    )

    first = registry.get("face_recognizer")
    assert registry.get("face_recognizer") is first
    assert registry.health().status is HealthLevel.HEALTHY
    assert validator_calls == 1


def test_manifest_loader_is_strict_and_resolves_paths_from_manifest_directory(
    tmp_path: Path,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    manifest = load_manifest(manifest_path)
    detector = next(model for model in manifest.models if model.name == "face_detector")
    assert detector.resolved_path == (
        tmp_path / "models" / "face_detector.bin"
    ).resolve()

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    "drift",
    [
        "manifest_version",
        "missing_model",
        "extra_model",
        "core_not_required",
        "optional_required",
    ],
)
def test_manifest_v1_rejects_role_or_version_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if drift == "manifest_version":
        payload["manifest_version"] = "test-v1"
    elif drift == "missing_model":
        payload["models"] = payload["models"][:-1]
    elif drift == "extra_model":
        extra = copy.deepcopy(payload["models"][0])
        extra["name"] = "unexpected_model"
        payload["models"].append(extra)
    elif drift == "core_not_required":
        payload["models"][0]["required"] = False
    elif drift == "optional_required":
        payload["models"][-1]["required"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_manifest(manifest_path)


def test_manifest_rejects_unknown_execution_provider(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["models"][0]["providers"] = ["UntrustedExecutionProvider"]
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_manifest(manifest_path)


def test_repository_manifest_matches_algorithm_config() -> None:
    project_root = Path(__file__).resolve().parents[3]
    manifest = load_manifest(project_root / "models.manifest.json")
    config = load_config(project_root / "config" / "algorithm.yaml")

    assert manifest.manifest_version == MANIFEST_VERSION
    for entry in manifest.models:
        configured = getattr(config.models, entry.name)
        assert entry.resolved_path == configured.path
        assert entry.sha256 == configured.sha256
        assert entry.backend == configured.backend
        assert entry.providers == configured.providers
        assert entry.expected.output_dimension == configured.expected_output_dimension
        assert entry.required is (entry.name in CORE_MODEL_NAMES)
        assert configured.enabled is (entry.name in CORE_MODEL_NAMES)
        if entry.name == "face_recognizer":
            assert entry.feature_version.value == config.feature_version
        else:
            assert entry.feature_version is None


def test_artifact_verifier_reports_optional_missing_as_degraded(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest(tmp_path, missing={OPTIONAL_MODEL_NAME})
    health = verify_manifest_artifacts(manifest_path)

    assert health.status is HealthLevel.DEGRADED
    assert all(model.state is ModelState.READY for model in health.models[:-1])
    assert health.models[-1].state is ModelState.UNAVAILABLE
