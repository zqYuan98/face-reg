from __future__ import annotations

from types import SimpleNamespace

import pytest

import face_algorithm.core.adapters as adapters
import face_algorithm.portrait.modnet as modnet
from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_factories import create_portrait_matting_factory


def test_portrait_factory_consumes_the_registry_verified_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = SimpleNamespace(data=b"verified")
    adapter = object()
    seen = []
    monkeypatch.setattr(
        modnet,
        "_create_modnet_adapter",
        lambda value, *, max_image_pixels: (
            seen.append((value, max_image_pixels)) or adapter
        ),
    )

    result = create_portrait_matting_factory(max_image_pixels=123)(artifact)

    assert result is adapter
    assert seen == [(artifact, 123)]


def test_process_registry_does_not_register_optional_modnet_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = __import__("pathlib").Path(__file__).resolve().parents[3]
    config = load_config(project_root / "config" / "algorithm.yaml")
    captured = {}
    sentinel = object()

    def recording_registry(manifest_path, factories, **kwargs):
        captured["manifest_path"] = manifest_path
        captured["factories"] = factories
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(adapters, "ModelRegistry", recording_registry)

    result = adapters.create_model_registry(
        project_root / "models.manifest.json",
        config,
    )

    assert result is sentinel
    assert set(captured["factories"]) == {
        "face_detector",
        "face_landmark_primary",
        "face_landmark_secondary",
        "face_recognizer",
        "rotation_classifier",
        "pose_var",
        "pose_conv",
    }
    assert config.models.portrait_matting.enabled is False


def test_process_registry_registers_optional_modnet_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = __import__("pathlib").Path(__file__).resolve().parents[3]
    config = load_config(project_root / "config" / "algorithm.yaml")
    models = config.models.model_copy(
        update={
            "portrait_matting": config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    config = config.model_copy(update={"models": models})
    captured = {}

    def recording_registry(_manifest_path, factories, **_kwargs):
        captured["factories"] = factories
        return object()

    monkeypatch.setattr(adapters, "ModelRegistry", recording_registry)

    adapters.create_model_registry(
        project_root / "models.manifest.json",
        config,
    )

    assert "portrait_matting" in captured["factories"]
