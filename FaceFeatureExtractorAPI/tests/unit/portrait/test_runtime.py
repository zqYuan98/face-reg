from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest
import torch

from face_algorithm.portrait import (
    PortraitMattingDisabledError,
    PortraitMattingModelInfo,
    PortraitMattingRuntime,
    build_portrait_matting_runtime,
)
from face_algorithm.portrait.modnet import _ModNetAdapter
from face_algorithm.portrait.runtime import _bind_portrait_matting_runtime
from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_registry import (
    LoadedModelHandle,
    VerifiedModelEvidence,
)


class ConstantMatte(torch.nn.Module):
    def forward(self, tensor, inference):
        assert inference is True
        matte = torch.full(
            (1, 1, tensor.shape[2], tensor.shape[3]),
            0.625,
            dtype=torch.float32,
        )
        return None, None, matte


class HandleRegistry:
    def __init__(self, handle: LoadedModelHandle) -> None:
        self.handle = handle
        self.calls: list[str] = []

    def get_with_evidence(self, name: str) -> LoadedModelHandle:
        self.calls.append(name)
        return self.handle


def _enabled_config():
    project_root = __import__("pathlib").Path(__file__).resolve().parents[3]
    config = load_config(project_root / "config" / "algorithm.yaml")
    models = config.models.model_copy(
        update={
            "portrait_matting": config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    return config.model_copy(update={"models": models})


def _handle(config) -> LoadedModelHandle:
    entry = config.models.portrait_matting
    evidence = VerifiedModelEvidence(
        name="portrait_matting",
        version=entry.version,
        sha256=entry.sha256,
        size=26_255_603,
        backend=entry.backend,
        providers=entry.providers,
        feature_version=None,
        output_dimension=None,
    )
    adapter = _ModNetAdapter(
        ConstantMatte().eval(),
        torch.device("cpu"),
        max_image_pixels=config.resources.max_image_pixels,
    )
    return LoadedModelHandle(instance=adapter, evidence=evidence)


def test_public_runtime_exposes_alpha_and_immutable_audit_metadata_only() -> None:
    config = _enabled_config()
    registry = HandleRegistry(_handle(config))

    runtime = _bind_portrait_matting_runtime(handle=registry.handle, config=config)
    alpha = runtime.matte(np.zeros((64, 96, 3), dtype=np.uint8))

    assert isinstance(runtime, PortraitMattingRuntime)
    assert isinstance(runtime.model_info, PortraitMattingModelInfo)
    assert runtime.model_info.name == "portrait_matting"
    assert runtime.model_info.version == config.models.portrait_matting.version
    assert runtime.model_info.sha256 == config.models.portrait_matting.sha256
    assert runtime.model_info.backend == "pytorch"
    assert runtime.model_info.providers == ("CPUExecutionProvider",)
    assert alpha.shape == (64, 96)
    assert np.all(alpha == np.float32(0.625))
    assert registry.calls == []
    assert not hasattr(runtime, "model")
    assert not hasattr(runtime, "adapter")
    with pytest.raises((AttributeError, FrozenInstanceError)):
        runtime.model_info = runtime.model_info


def test_disabled_portrait_runtime_fails_before_touching_registry() -> None:
    config = _enabled_config()
    disabled_models = config.models.model_copy(
        update={
            "portrait_matting": config.models.portrait_matting.model_copy(
                update={"enabled": False}
            )
        }
    )
    config = config.model_copy(update={"models": disabled_models})
    registry = HandleRegistry(_handle(_enabled_config()))

    with pytest.raises(PortraitMattingDisabledError):
        build_portrait_matting_runtime(registry=registry, config=config)

    assert registry.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "other"),
        ("version", "other"),
        ("sha256", "0" * 64),
        ("backend", "onnxruntime"),
        ("providers", ()),
        ("feature_version", "recognizer_v1_1024"),
        ("output_dimension", 1),
    ],
)
def test_builder_rejects_runtime_evidence_drift(field: str, value: object) -> None:
    config = _enabled_config()
    evidence = _handle(config).evidence
    values = {
        "name": evidence.name,
        "version": evidence.version,
        "sha256": evidence.sha256,
        "size": evidence.size,
        "backend": evidence.backend,
        "providers": evidence.providers,
        "feature_version": evidence.feature_version,
        "output_dimension": evidence.output_dimension,
    }
    values[field] = value
    handle = LoadedModelHandle(
        instance=_ModNetAdapter(
            ConstantMatte().eval(),
            torch.device("cpu"),
            max_image_pixels=config.resources.max_image_pixels,
        ),
        evidence=VerifiedModelEvidence(**values),
    )

    with pytest.raises(ValueError, match="evidence"):
        _bind_portrait_matting_runtime(handle=handle, config=config)


def test_builder_rejects_non_modnet_registry_instance() -> None:
    config = _enabled_config()
    valid = _handle(config)
    handle = LoadedModelHandle(instance=object(), evidence=valid.evidence)

    with pytest.raises(TypeError):
        _bind_portrait_matting_runtime(handle=handle, config=config)
