"""Offline MODNet portrait-matting adapter."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from io import BytesIO
from typing import Final

import cv2
import numpy as np
import torch

from face_algorithm.core.contracts import validate_image_input
from face_algorithm.portrait._modnet_arch import MODNet
from face_algorithm.runtime.model_registry import VerifiedArtifact


_REFERENCE_SIZE: Final = 512
_MODEL_STRIDE: Final = 32
_MAX_MODEL_INPUT_PIXELS: Final = 1_048_576
_MAX_MODEL_INPUT_SIDE: Final = 2_048
_MAX_MODEL_ASPECT_RATIO: Final = 4.0


def _validate_bgr_image(image: object) -> np.ndarray:
    if not isinstance(image, np.ndarray):
        raise TypeError("image must be a NumPy ndarray")
    if image.dtype != np.uint8:
        raise ValueError("image must have dtype uint8")
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must have shape HxWx3")
    if image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError("image dimensions must be positive")
    return image


def _preprocess_modnet_input(
    image: object,
) -> tuple[torch.Tensor, tuple[int, int]]:
    """Apply the fixed upstream MODNet photographic preprocessing contract."""

    bgr = _validate_bgr_image(image)
    original_height, original_width = bgr.shape[:2]
    resized_height, resized_width = original_height, original_width

    if max(original_height, original_width) < _REFERENCE_SIZE or min(
        original_height,
        original_width,
    ) > _REFERENCE_SIZE:
        if original_width >= original_height:
            resized_height = _REFERENCE_SIZE
            resized_width = int(
                original_width / original_height * _REFERENCE_SIZE
            )
        else:
            resized_width = _REFERENCE_SIZE
            resized_height = int(
                original_height / original_width * _REFERENCE_SIZE
            )

    resized_height -= resized_height % _MODEL_STRIDE
    resized_width -= resized_width % _MODEL_STRIDE
    if resized_height <= 0 or resized_width <= 0:
        raise ValueError("image is too small for MODNet's 32-pixel stride")
    derived_pixels = resized_height * resized_width
    derived_aspect_ratio = max(resized_height, resized_width) / min(
        resized_height,
        resized_width,
    )
    if (
        derived_pixels > _MAX_MODEL_INPUT_PIXELS
        or max(resized_height, resized_width) > _MAX_MODEL_INPUT_SIDE
        or derived_aspect_ratio > _MAX_MODEL_ASPECT_RATIO
    ):
        raise ValueError("image exceeds MODNet safe preprocessing limits")

    resized = cv2.resize(
        bgr,
        (resized_width, resized_height),
        interpolation=cv2.INTER_AREA,
    )
    rgb = resized[:, :, ::-1].copy()
    normalized = rgb.astype(np.float32) / np.float32(127.5) - np.float32(1.0)
    chw = np.ascontiguousarray(normalized.transpose(2, 0, 1)[None, ...])
    return torch.from_numpy(chw), (original_height, original_width)


def _deserialize_state_dict(
    checkpoint_bytes: bytes,
    device: torch.device,
) -> OrderedDict[str, torch.Tensor]:
    if not isinstance(checkpoint_bytes, bytes):
        raise TypeError("checkpoint must be an immutable byte buffer")
    payload = torch.load(
        BytesIO(checkpoint_bytes),
        map_location=device,
        weights_only=True,
    )
    if not isinstance(payload, Mapping):
        raise TypeError("MODNet checkpoint must be a state-dict mapping")
    if not payload:
        raise ValueError("MODNet checkpoint state dict must not be empty")

    normalized: OrderedDict[str, torch.Tensor] = OrderedDict()
    for raw_key, value in payload.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise TypeError("MODNet checkpoint keys must be non-empty strings")
        if not isinstance(value, torch.Tensor):
            raise TypeError("MODNet checkpoint values must be tensors")
        key = raw_key.removeprefix("module.")
        if not key:
            raise ValueError("MODNet checkpoint key is empty after normalization")
        if key in normalized:
            raise ValueError("MODNet checkpoint key collision after prefix removal")
        normalized[key] = value
    return normalized


class _ModNetAdapter:
    """Internal model owner exposing only the alpha-matte operation."""

    __slots__ = ("_device", "_max_image_pixels", "_model")

    def __init__(
        self,
        model: torch.nn.Module,
        device: torch.device,
        *,
        max_image_pixels: int,
    ) -> None:
        if not isinstance(model, torch.nn.Module):
            raise TypeError("model must be a torch module")
        if not isinstance(device, torch.device):
            raise TypeError("device must be a torch device")
        if (
            isinstance(max_image_pixels, bool)
            or not isinstance(max_image_pixels, int)
            or max_image_pixels <= 0
        ):
            raise ValueError("max_image_pixels must be a positive integer")
        self._model = model
        self._device = device
        self._max_image_pixels = max_image_pixels

    def get_providers(self) -> tuple[str, ...]:
        """Report the actual execution provider used by this CPU adapter."""

        return ("CPUExecutionProvider",)

    def matte(self, image: object) -> np.ndarray:
        validate_image_input(image, self._max_image_pixels)
        tensor, original_size = _preprocess_modnet_input(image)
        tensor = tensor.to(self._device)
        with torch.inference_mode():
            outputs = self._model(tensor, True)
        if not isinstance(outputs, (tuple, list)) or len(outputs) != 3:
            raise TypeError("MODNet must return exactly three outputs")
        matte = outputs[2]
        if not isinstance(matte, torch.Tensor):
            raise TypeError("MODNet matte output must be a torch tensor")
        expected_shape = (1, 1, tensor.shape[2], tensor.shape[3])
        if tuple(matte.shape) != expected_shape:
            raise ValueError(
                f"MODNet matte shape is {tuple(matte.shape)}, expected {expected_shape}"
            )
        if matte.dtype != torch.float32:
            raise ValueError("MODNet matte output must have dtype float32")
        if not bool(torch.isfinite(matte).all().item()):
            raise ValueError("MODNet matte output must be finite")

        alpha = matte.detach().to("cpu").numpy()[0, 0]
        original_height, original_width = original_size
        restored = cv2.resize(
            alpha,
            (original_width, original_height),
            interpolation=cv2.INTER_AREA,
        )
        clipped = np.clip(restored, np.float32(0.0), np.float32(1.0))
        return np.ascontiguousarray(clipped, dtype=np.float32)


def _create_modnet_adapter(
    artifact: VerifiedArtifact,
    *,
    max_image_pixels: int,
) -> _ModNetAdapter:
    """Construct MODNet solely from bytes already verified by ModelRegistry."""

    data = getattr(artifact, "data", None)
    if not isinstance(data, bytes):
        raise TypeError("verified MODNet artifact must contain immutable bytes")
    device = torch.device("cpu")
    state_dict = _deserialize_state_dict(data, device)
    model = MODNet(backbone_pretrained=False)
    model.load_state_dict(state_dict, strict=True)
    model.requires_grad_(False)
    model.to(device)
    model.eval()
    return _ModNetAdapter(
        model,
        device,
        max_image_pixels=max_image_pixels,
    )
