from __future__ import annotations

from collections import OrderedDict
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
import torch

import face_algorithm.portrait.modnet as modnet
from face_algorithm.core.contracts import ImageTooLargeError
from face_algorithm.portrait.modnet import (
    _ModNetAdapter,
    _create_modnet_adapter,
    _deserialize_state_dict,
)


class RecordingModel(torch.nn.Module):
    def __init__(self, matte_factory=None) -> None:
        super().__init__()
        self.matte_factory = matte_factory
        self.loaded_state = None
        self.loaded_strict = None
        self.forward_input = None
        self.forward_inference = None
        self.forward_used_inference_mode = None

    def load_state_dict(self, state_dict, strict=True):
        self.loaded_state = state_dict
        self.loaded_strict = strict
        return SimpleNamespace(missing_keys=[], unexpected_keys=[])

    def forward(self, tensor, inference):
        self.forward_input = tensor.detach().clone()
        self.forward_inference = inference
        self.forward_used_inference_mode = torch.is_inference_mode_enabled()
        if self.matte_factory is None:
            matte = torch.full(
                (1, 1, tensor.shape[2], tensor.shape[3]),
                0.5,
                dtype=torch.float32,
                device=tensor.device,
            )
        else:
            matte = self.matte_factory(tensor)
        return None, None, matte


def test_deserialize_uses_bytesio_cpu_and_weights_only(monkeypatch) -> None:
    calls = []
    state = OrderedDict([("module.weight", torch.ones(1))])

    def fake_load(stream, *, map_location, weights_only):
        calls.append((stream, map_location, weights_only))
        return state

    monkeypatch.setattr(modnet.torch, "load", fake_load)

    result = _deserialize_state_dict(b"checkpoint", torch.device("cpu"))

    assert result == {"weight": state["module.weight"]}
    assert len(calls) == 1
    stream, map_location, weights_only = calls[0]
    assert isinstance(stream, BytesIO)
    assert stream.getvalue() == b"checkpoint"
    assert map_location == torch.device("cpu")
    assert weights_only is True


def test_deserialize_strips_exactly_one_module_prefix(monkeypatch) -> None:
    state = OrderedDict(
        [
            ("module.first", torch.ones(1)),
            ("module.module.second", torch.ones(1) * 2),
            ("plain", torch.ones(1) * 3),
        ]
    )
    monkeypatch.setattr(modnet.torch, "load", lambda *_args, **_kwargs: state)

    result = _deserialize_state_dict(b"checkpoint", torch.device("cpu"))

    assert tuple(result) == ("first", "module.second", "plain")


@pytest.mark.parametrize(
    "payload",
    [
        [torch.ones(1)],
        {"state_dict": {"module.weight": torch.ones(1)}},
        {1: torch.ones(1)},
        {"weight": "not-a-tensor"},
        {},
    ],
)
def test_deserialize_rejects_non_state_dict_checkpoint_structures(
    monkeypatch,
    payload,
) -> None:
    monkeypatch.setattr(modnet.torch, "load", lambda *_args, **_kwargs: payload)

    with pytest.raises((TypeError, ValueError)):
        _deserialize_state_dict(b"checkpoint", torch.device("cpu"))


def test_deserialize_rejects_key_collision_after_prefix_removal(monkeypatch) -> None:
    state = OrderedDict(
        [
            ("module.weight", torch.ones(1)),
            ("weight", torch.ones(1) * 2),
        ]
    )
    monkeypatch.setattr(modnet.torch, "load", lambda *_args, **_kwargs: state)

    with pytest.raises(ValueError, match="collision"):
        _deserialize_state_dict(b"checkpoint", torch.device("cpu"))


def test_factory_builds_inference_only_eval_model_and_strictly_loads_state(
    monkeypatch,
) -> None:
    model = RecordingModel()
    monkeypatch.setattr(modnet, "MODNet", lambda **kwargs: model)
    monkeypatch.setattr(
        modnet,
        "_deserialize_state_dict",
        lambda *_args, **_kwargs: {"weight": torch.ones(1)},
    )
    artifact = SimpleNamespace(data=b"checkpoint")

    adapter = _create_modnet_adapter(
        artifact,
        max_image_pixels=25_000_000,
    )

    assert isinstance(adapter, _ModNetAdapter)
    assert model.loaded_strict is True
    assert tuple(model.loaded_state) == ("weight",)
    assert model.training is False


def test_factory_propagates_strict_state_dict_mismatch(monkeypatch) -> None:
    class RejectingModel(RecordingModel):
        def load_state_dict(self, state_dict, strict=True):
            raise RuntimeError("missing keys")

    monkeypatch.setattr(modnet, "MODNet", lambda **kwargs: RejectingModel())
    monkeypatch.setattr(
        modnet,
        "_deserialize_state_dict",
        lambda *_args, **_kwargs: {"wrong": torch.ones(1)},
    )

    with pytest.raises(RuntimeError, match="missing keys"):
        _create_modnet_adapter(
            SimpleNamespace(data=b"checkpoint"),
            max_image_pixels=25_000_000,
        )


def test_adapter_runs_third_output_in_inference_mode_and_restores_original_size() -> None:
    def matte_factory(tensor):
        height, width = tensor.shape[2:]
        values = torch.linspace(-0.25, 1.25, height * width, dtype=torch.float32)
        return values.reshape(1, 1, height, width)

    model = RecordingModel(matte_factory)
    adapter = _ModNetAdapter(
        model,
        torch.device("cpu"),
        max_image_pixels=25_000_000,
    )
    image = np.full((200, 400, 3), (5, 100, 200), dtype=np.uint8)
    before = image.copy()

    alpha = adapter.matte(image)

    assert alpha.shape == (200, 400)
    assert alpha.dtype == np.float32
    assert alpha.flags.c_contiguous
    assert np.all(np.isfinite(alpha))
    assert float(alpha.min()) == pytest.approx(0.0)
    assert float(alpha.max()) == pytest.approx(1.0)
    assert model.forward_input.shape == (1, 3, 512, 1024)
    assert model.forward_inference is True
    assert model.forward_used_inference_mode is True
    assert np.array_equal(image, before)


def test_adapter_rejects_over_pixel_limit_before_model_or_resize() -> None:
    model = RecordingModel()
    adapter = _ModNetAdapter(
        model,
        torch.device("cpu"),
        max_image_pixels=25_000_000,
    )
    base = np.zeros(3, dtype=np.uint8)
    oversized = np.lib.stride_tricks.as_strided(
        base,
        shape=(5_000, 5_001, 3),
        strides=(0, 0, 1),
        writeable=False,
    )

    with pytest.raises(ImageTooLargeError):
        adapter.matte(oversized)

    assert model.forward_input is None


def test_adapter_allows_an_image_at_the_exact_pixel_limit() -> None:
    model = RecordingModel()
    adapter = _ModNetAdapter(
        model,
        torch.device("cpu"),
        max_image_pixels=16,
    )

    alpha = adapter.matte(np.zeros((4, 4, 3), dtype=np.uint8))

    assert alpha.shape == (4, 4)
    assert model.forward_input is not None


@pytest.mark.parametrize(
    "matte_factory",
    [
        lambda tensor: np.zeros((1, 1, tensor.shape[2], tensor.shape[3]), np.float32),
        lambda tensor: torch.zeros((1, tensor.shape[2], tensor.shape[3])),
        lambda tensor: torch.zeros((2, 1, tensor.shape[2], tensor.shape[3])),
        lambda tensor: torch.zeros((1, 2, tensor.shape[2], tensor.shape[3])),
        lambda tensor: torch.zeros(
            (1, 1, tensor.shape[2], tensor.shape[3]), dtype=torch.float64
        ),
        lambda tensor: torch.full(
            (1, 1, tensor.shape[2], tensor.shape[3]),
            float("nan"),
            dtype=torch.float32,
        ),
        lambda tensor: torch.full(
            (1, 1, tensor.shape[2], tensor.shape[3]),
            float("inf"),
            dtype=torch.float32,
        ),
    ],
)
def test_adapter_rejects_invalid_or_nonfinite_third_output(matte_factory) -> None:
    adapter = _ModNetAdapter(
        RecordingModel(matte_factory),
        torch.device("cpu"),
        max_image_pixels=25_000_000,
    )

    with pytest.raises((TypeError, ValueError)):
        adapter.matte(np.zeros((64, 64, 3), dtype=np.uint8))


@pytest.mark.parametrize(
    "outputs",
    [
        None,
        (),
        (None, None),
        (None, None, torch.zeros((1, 1, 512, 512)), "extra"),
    ],
)
def test_adapter_rejects_invalid_model_output_tuple(outputs) -> None:
    class InvalidOutputModel(RecordingModel):
        def forward(self, tensor, inference):
            return outputs

    adapter = _ModNetAdapter(
        InvalidOutputModel(),
        torch.device("cpu"),
        max_image_pixels=25_000_000,
    )

    with pytest.raises((TypeError, ValueError)):
        adapter.matte(np.zeros((64, 64, 3), dtype=np.uint8))
