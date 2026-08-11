from __future__ import annotations

import cv2
import numpy as np
import pytest
import torch

from face_algorithm.portrait.modnet import _preprocess_modnet_input


@pytest.mark.parametrize(
    ("height", "width", "expected_height", "expected_width"),
    [
        (200, 400, 512, 1024),
        (400, 200, 1024, 512),
        (720, 1280, 512, 896),
        (1280, 720, 896, 512),
        (480, 640, 480, 640),
        (512, 513, 512, 512),
        (511, 513, 480, 512),
        (513, 1025, 512, 992),
    ],
)
def test_preprocess_uses_official_ref_size_and_multiple_of_32_contract(
    height: int,
    width: int,
    expected_height: int,
    expected_width: int,
) -> None:
    image = np.zeros((height, width, 3), dtype=np.uint8)

    tensor, original_size = _preprocess_modnet_input(image)

    assert original_size == (height, width)
    assert tensor.shape == (1, 3, expected_height, expected_width)
    assert tensor.dtype is torch.float32
    assert expected_height % 32 == 0
    assert expected_width % 32 == 0


def test_preprocess_converts_bgr_to_rgb_and_normalizes_float32() -> None:
    image = np.empty((32, 32, 3), dtype=np.uint8)
    image[...] = (0, 127, 255)

    tensor, _ = _preprocess_modnet_input(image)

    assert tensor.dtype is torch.float32
    assert tensor.shape == (1, 3, 512, 512)
    assert tensor[0, :, 0, 0].numpy() == pytest.approx(
        np.asarray([1.0, 127.0 / 127.5 - 1.0, -1.0], dtype=np.float32),
        abs=1e-7,
    )


def test_preprocess_uses_area_interpolation_and_does_not_mutate_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.arange(64 * 96 * 3, dtype=np.uint8).reshape(64, 96, 3)
    before = image.copy()
    original_resize = cv2.resize
    interpolations: list[int] = []

    def recording_resize(*args, **kwargs):
        interpolations.append(kwargs.get("interpolation"))
        return original_resize(*args, **kwargs)

    monkeypatch.setattr("face_algorithm.portrait.modnet.cv2.resize", recording_resize)

    tensor, original_size = _preprocess_modnet_input(image)

    assert original_size == (64, 96)
    assert tensor.is_contiguous()
    assert interpolations == [cv2.INTER_AREA]
    assert np.array_equal(image, before)


def test_preprocess_rejects_extreme_aspect_ratio_before_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = np.zeros(3, dtype=np.uint8)
    image = np.lib.stride_tricks.as_strided(
        base,
        shape=(1, 511, 3),
        strides=(0, 0, 1),
        writeable=False,
    )

    def forbidden_resize(*_args, **_kwargs):
        raise AssertionError("unsafe derived resize was reached")

    monkeypatch.setattr("face_algorithm.portrait.modnet.cv2.resize", forbidden_resize)

    with pytest.raises(ValueError, match="safe preprocessing limits"):
        _preprocess_modnet_input(image)


def test_preprocess_allows_the_exact_safe_derived_shape_boundary() -> None:
    image = np.zeros((512, 2048, 3), dtype=np.uint8)

    tensor, original_size = _preprocess_modnet_input(image)

    assert original_size == (512, 2048)
    assert tensor.shape == (1, 3, 512, 2048)


def test_preprocess_rejects_one_stride_over_safe_boundary_before_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = np.zeros(3, dtype=np.uint8)
    image = np.lib.stride_tricks.as_strided(
        base,
        shape=(512, 2080, 3),
        strides=(0, 0, 1),
        writeable=False,
    )

    def forbidden_resize(*_args, **_kwargs):
        raise AssertionError("unsafe derived resize was reached")

    monkeypatch.setattr("face_algorithm.portrait.modnet.cv2.resize", forbidden_resize)

    with pytest.raises(ValueError, match="safe preprocessing limits"):
        _preprocess_modnet_input(image)


@pytest.mark.parametrize(
    "image",
    [
        [[[]]],
        np.zeros((32, 32, 3), dtype=np.float32),
        np.zeros((32, 32), dtype=np.uint8),
        np.zeros((32, 32, 4), dtype=np.uint8),
        np.zeros((0, 32, 3), dtype=np.uint8),
        np.zeros((16, 1000, 3), dtype=np.uint8),
    ],
)
def test_preprocess_rejects_invalid_or_too_small_images(image: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        _preprocess_modnet_input(image)
