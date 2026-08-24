from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pytest

from api.services import AlgorithmDeadlineExceeded, DefaultAlgorithmServices
from face_algorithm.runtime.calibration import CalibrationLoadError


class _Health:
    def __init__(
        self,
        *,
        matting_state: str = "unavailable",
        model_states: dict[str, str] | None = None,
    ) -> None:
        self._matting_state = matting_state
        self._model_states = dict(model_states or {})

    def model_dump(self, *, mode: str):
        assert mode == "json"
        return {
            "status": "healthy",
            "models": [
                {"name": name, "state": self._model_states.get(name, "ready")}
                for name in (
                    "face_detector",
                    "face_landmark_primary",
                    "face_landmark_secondary",
                    "face_recognizer",
                    "rotation_classifier",
                    "pose_var",
                    "pose_conv",
                )
            ] + [
                {"name": "portrait_matting", "state": self._matting_state},
            ],
        }


class _Registry:
    def __init__(
        self,
        *,
        matting_state: str = "unavailable",
        model_states: dict[str, str] | None = None,
    ) -> None:
        self._matting_state = matting_state
        self._model_states = dict(model_states or {})
        self.manifest = SimpleNamespace(
            models=tuple(
                SimpleNamespace(name=name)
                for name in (
                    "face_detector",
                    "face_landmark_primary",
                    "face_landmark_secondary",
                    "face_recognizer",
                    "rotation_classifier",
                    "pose_var",
                    "pose_conv",
                    "portrait_matting",
                )
            )
        )
        self.reset_names: list[str] = []

    def health(self):
        return _Health(
            matting_state=self._matting_state,
            model_states=self._model_states,
        )

    def reset(self, name: str) -> None:
        self.reset_names.append(name)


def test_start_snapshots_health_then_releases_probe_model_instances(algorithm_config) -> None:
    registry = _Registry()
    services = DefaultAlgorithmServices(config=algorithm_config, registry=registry)

    services.start()

    assert services.readiness()["ready"] is True
    assert registry.reset_names == [model.name for model in registry.manifest.models]


def _artifact_config(algorithm_config):
    image = algorithm_config.image_verification.model_copy(update={"mode": "artifact"})
    video_calibration = algorithm_config.video_verification.calibration.model_copy(
        update={"mode": "artifact"}
    )
    video = algorithm_config.video_verification.model_copy(
        update={"calibration": video_calibration}
    )
    return algorithm_config.model_copy(
        update={"image_verification": image, "video_verification": video}
    )


def test_artifact_calibration_without_independent_trust_store_fails_at_startup(
    algorithm_config,
) -> None:
    services = DefaultAlgorithmServices(
        config=_artifact_config(algorithm_config),
        registry=_Registry(),
    )

    with pytest.raises(CalibrationLoadError, match="trust store"):
        services.start()


def test_explicit_calibration_trust_store_is_passed_to_image_and_video_builders(
    algorithm_config,
    monkeypatch,
) -> None:
    store = object()
    captured: list[object] = []

    class ImageRuntime:
        def verify(self, reference, probe):
            return {"kind": "image"}

    class VideoRuntime:
        def quick_verify(self, reference, path, *, deadline_monotonic):
            return {"kind": "video"}

    def build_image(**kwargs):
        captured.append(kwargs["key_store"])
        return ImageRuntime()

    def build_video(**kwargs):
        captured.append(kwargs["key_store"])
        return VideoRuntime()

    monkeypatch.setattr("api.services.build_image_verification_runtime", build_image)
    monkeypatch.setattr("api.services.build_video_verification_runtime", build_video)
    services = DefaultAlgorithmServices(
        config=_artifact_config(algorithm_config),
        registry=_Registry(),
        calibration_key_store=store,
    )
    services.start()
    assert captured == [store, store]
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    deadline = time.monotonic() + 1.0

    assert services.verify_images(
        image,
        image,
        deadline_monotonic=deadline,
    ) == {"kind": "image"}
    assert services.verify_video(
        image,
        __file__,
        mode="quick",
        deadline_monotonic=deadline,
    ) == {"kind": "video"}
    assert captured == [store, store]


def test_artifact_runtime_build_failure_clears_readiness(
    algorithm_config,
    monkeypatch,
) -> None:
    def reject_calibration(**kwargs):
        raise CalibrationLoadError("sensitive signature path")

    monkeypatch.setattr(
        "api.services.build_image_verification_runtime",
        reject_calibration,
    )
    services = DefaultAlgorithmServices(
        config=_artifact_config(algorithm_config),
        registry=_Registry(),
        calibration_key_store=object(),
    )

    with pytest.raises(CalibrationLoadError):
        services.start()

    assert services.readiness()["ready"] is False


def test_lazy_runtime_releases_temporary_source_registry_owners(
    algorithm_config,
    monkeypatch,
) -> None:
    registry = _Registry()
    services = DefaultAlgorithmServices(config=algorithm_config, registry=registry)
    services.start()
    registry.reset_names.clear()

    class Runtime:
        def verify(self, reference, probe):
            return {"ok": True}

    monkeypatch.setattr(
        "api.services.build_image_verification_runtime",
        lambda **kwargs: Runtime(),
    )
    image = np.zeros((8, 8, 3), dtype=np.uint8)

    assert services.verify_images(
        image,
        image,
        deadline_monotonic=time.monotonic() + 1.0,
    ) == {"ok": True}
    assert registry.reset_names == [model.name for model in registry.manifest.models]


def test_witness_runtime_is_lazy_singleton_and_releases_source_owners(
    algorithm_config,
    monkeypatch,
) -> None:
    registry = _Registry()
    services = DefaultAlgorithmServices(config=algorithm_config, registry=registry)
    services.start()
    registry.reset_names.clear()
    builds: list[tuple[object, object]] = []
    calls: list[np.ndarray] = []

    class Runtime:
        def blur(self, image):
            calls.append(image)
            return {"kind": "witness"}

    def build_witness(**kwargs):
        builds.append((kwargs["config"], kwargs["registry"]))
        return Runtime()

    monkeypatch.setattr("api.services.build_witness_blur_runtime", build_witness)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    deadline = time.monotonic() + 1.0

    assert services.blur_witness_background(
        image,
        deadline_monotonic=deadline,
    ) == {"kind": "witness"}
    assert services.blur_witness_background(
        image,
        deadline_monotonic=deadline,
    ) == {"kind": "witness"}
    assert builds == [(algorithm_config, registry)]
    assert calls == [image, image]
    assert registry.reset_names == [model.name for model in registry.manifest.models]
    services.close()
    assert services._witness is None


def test_expired_witness_deadline_fails_before_runtime_build(
    algorithm_config,
    monkeypatch,
) -> None:
    services = DefaultAlgorithmServices(config=algorithm_config, registry=_Registry())
    built = False

    def forbidden_build(**kwargs):
        nonlocal built
        built = True
        raise AssertionError("expired request must not build a runtime")

    monkeypatch.setattr("api.services.build_witness_blur_runtime", forbidden_build)
    with pytest.raises(AlgorithmDeadlineExceeded):
        services.blur_witness_background(
            np.zeros((8, 8, 3), dtype=np.uint8),
            deadline_monotonic=time.monotonic() - 1.0,
        )
    assert built is False


def test_failed_witness_build_releases_sources_and_can_retry(
    algorithm_config,
    monkeypatch,
) -> None:
    registry = _Registry()
    services = DefaultAlgorithmServices(config=algorithm_config, registry=registry)
    attempts = 0

    class Runtime:
        def blur(self, image):
            return {"ok": True}

    def flaky_build(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient build failure")
        return Runtime()

    monkeypatch.setattr("api.services.build_witness_blur_runtime", flaky_build)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    deadline = time.monotonic() + 1.0

    with pytest.raises(RuntimeError, match="transient"):
        services.blur_witness_background(image, deadline_monotonic=deadline)
    assert services._witness is None
    assert registry.reset_names == [model.name for model in registry.manifest.models]

    registry.reset_names.clear()
    assert services.blur_witness_background(
        image,
        deadline_monotonic=deadline,
    ) == {"ok": True}
    assert attempts == 2
    assert registry.reset_names == [model.name for model in registry.manifest.models]


def test_witness_readiness_tracks_enabled_verified_matting_model(
    algorithm_config,
) -> None:
    models = algorithm_config.models.model_copy(
        update={
            "portrait_matting": algorithm_config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    services = DefaultAlgorithmServices(
        config=algorithm_config.model_copy(update={"models": models}),
        registry=_Registry(matting_state="ready"),
    )

    services.start()

    assert services.readiness()["capabilities"]["witness_blur"] == "ready"


def test_witness_readiness_does_not_require_pose_or_recognizer_models(
    algorithm_config,
) -> None:
    models = algorithm_config.models.model_copy(
        update={
            "portrait_matting": algorithm_config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    registry = _Registry(
        matting_state="ready",
        model_states={
            "face_recognizer": "unavailable",
            "pose_var": "unavailable",
            "pose_conv": "unavailable",
        },
    )
    services = DefaultAlgorithmServices(
        config=algorithm_config.model_copy(update={"models": models}),
        registry=registry,
    )

    services.start()
    readiness = services.readiness()

    assert readiness["ready"] is False
    assert readiness["capabilities"]["image_verification"] == "unavailable"
    assert readiness["capabilities"]["video_verification"] == "unavailable"
    assert readiness["capabilities"]["witness_blur"] == "ready"


@pytest.mark.parametrize(
    "missing",
    [
        "rotation_classifier",
        "face_detector",
        "face_landmark_primary",
        "face_landmark_secondary",
    ],
)
def test_witness_readiness_requires_each_executed_face_model(
    algorithm_config,
    missing,
) -> None:
    models = algorithm_config.models.model_copy(
        update={
            "portrait_matting": algorithm_config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    services = DefaultAlgorithmServices(
        config=algorithm_config.model_copy(update={"models": models}),
        registry=_Registry(
            matting_state="ready",
            model_states={missing: "unavailable"},
        ),
    )

    services.start()

    assert services.readiness()["capabilities"]["witness_blur"] == "unavailable"
