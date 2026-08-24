from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from face_algorithm.schemas import CardRegion, FaceBox, LandmarkPoint, ReasonCode
from face_algorithm.witness.blur import (
    _WitnessBlurError,
    _blur_witness_pixels,
    _card_mask,
    _select_witness_subject_matte,
)


FACE = FaceBox(x1=90.0, y1=45.0, x2=150.0, y2=125.0, confidence=0.99)
CARD = CardRegion(
    corners=(
        LandmarkPoint(x=75.0, y=145.0),
        LandmarkPoint(x=165.0, y=145.0),
        LandmarkPoint(x=165.0, y=195.0),
        LandmarkPoint(x=75.0, y=195.0),
    ),
    confidence=0.95,
)


def _options(**updates):
    values = {
        "blur_sigma_ratio": 0.025,
        "blur_kernel_cap": 51,
        "blur_working_max_dimension": 1600,
        "mask_threshold": 0.50,
        "subject_edge_alpha": 0.15,
        "min_subject_area_ratio": 0.05,
        "max_subject_area_ratio": 0.92,
        "card_margin_ratio": 0.02,
        "feather_radius_ratio": 0.01,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _image() -> np.ndarray:
    y, x = np.indices((240, 240))
    checker = (((x // 3 + y // 3) % 2) * 180 + 30).astype(np.uint8)
    image = np.stack((checker, np.roll(checker, 1, axis=0), checker), axis=2)
    image[30:225, 55:185] = (30, 100, 180)
    image[145:196, 75:166] = (240, 240, 240)
    return np.ascontiguousarray(image)


def _matte() -> np.ndarray:
    matte = np.zeros((240, 240), dtype=np.float32)
    matte[25:225, 50:190] = 1.0
    return matte


def test_selects_face_connected_soft_subject_and_rejects_distractor() -> None:
    matte = _matte()
    matte[40:200, 5:25] = 1.0

    subject, quality = _select_witness_subject_matte(matte, FACE, _options())

    assert subject[100, 100] == pytest.approx(1.0)
    assert subject[100, 10] == pytest.approx(0.0)
    assert quality.subject_area_ratio > 0.05


@pytest.mark.parametrize("edge", ["top", "left", "right", "bottom"])
def test_visible_subject_can_extend_to_any_image_edge_and_remains_protected(
    edge: str,
) -> None:
    matte = _matte()
    if edge == "top":
        matte[0:25, 100:105] = 0.20
    elif edge == "left":
        matte[100:105, 0:50] = 0.20
    elif edge == "right":
        matte[100:105, 190:240] = 0.20
    else:
        matte[225:240, 100:105] = 0.20

    subject, quality = _select_witness_subject_matte(matte, FACE, _options())

    assert float(subject.max()) == pytest.approx(1.0)
    assert quality.face_mean == pytest.approx(1.0)


def test_low_face_alpha_and_malformed_matte_fail_closed() -> None:
    low = _matte()
    low[45:125, 90:150] = 0.20
    with pytest.raises(_WitnessBlurError) as caught:
        _select_witness_subject_matte(low, FACE, _options())
    assert caught.value.reason_code is ReasonCode.MATTING_LOW_CONFIDENCE

    with pytest.raises(TypeError):
        _select_witness_subject_matte(
            np.full((240, 240), np.nan, dtype=np.float32), FACE, _options()
        )


def test_subject_area_upper_bound_is_inclusive_then_fails_closed() -> None:
    face = FaceBox(x1=30.0, y1=20.0, x2=70.0, y2=70.0, confidence=0.99)
    exact = np.zeros((100, 100), dtype=np.float32)
    exact[:90, :] = 1.0
    over = exact.copy()
    over[90, 0] = 1.0
    options = _options(
        min_subject_area_ratio=0.01,
        max_subject_area_ratio=0.90,
    )

    subject, quality = _select_witness_subject_matte(exact, face, options)

    assert subject.shape == exact.shape
    assert quality.subject_area_ratio == pytest.approx(0.90)
    with pytest.raises(_WitnessBlurError) as caught:
        _select_witness_subject_matte(over, face, options)
    assert caught.value.reason_code is ReasonCode.MATTING_LOW_CONFIDENCE


def test_soft_alpha_haze_cannot_consume_all_blurrable_background() -> None:
    haze = np.full((240, 240), 0.20, dtype=np.float32)
    haze[25:225, 50:190] = 1.0

    with pytest.raises(_WitnessBlurError) as caught:
        _select_witness_subject_matte(haze, FACE, _options())

    assert caught.value.reason_code is ReasonCode.MATTING_LOW_CONFIDENCE


def test_person_and_card_hard_cores_are_pixel_exact_and_input_is_unchanged() -> None:
    image = _image()
    original = image.copy()
    matte = _matte()
    matte[145:196, 75:166] = 0.0  # MODNet missed the held card entirely.

    output, summary = _blur_witness_pixels(image, matte, (CARD,), _options())

    np.testing.assert_array_equal(image, original)
    hard_core = np.maximum(
        np.asarray(matte >= 0.50, np.uint8),
        _card_mask(image.shape[:2], (CARD,), 0.02),
    )
    np.testing.assert_array_equal(output[hard_core > 0], image[hard_core > 0])
    assert output.dtype == np.uint8
    assert output.flags.c_contiguous
    assert output.shape == image.shape
    assert summary.protected_area_ratio > 0.0


def test_background_high_frequency_is_reduced() -> None:
    image = _image()
    output, _summary = _blur_witness_pixels(image, _matte(), (CARD,), _options())
    background = np.zeros(image.shape[:2], dtype=np.uint8)
    background[:, :] = 1
    background[20:230, 45:195] = 0
    before = cv2.Laplacian(image, cv2.CV_32F)[background > 0].var()
    after = cv2.Laplacian(output, cv2.CV_32F)[background > 0].var()

    assert after < before * 0.35


def test_broad_soft_matte_without_local_samples_uses_safe_blur_fallback() -> None:
    y, x = np.indices((240, 240))
    checker = (((x + y) % 2) * 220 + 20).astype(np.uint8)
    image = np.ascontiguousarray(np.repeat(checker[:, :, None], 3, axis=2))
    matte = np.zeros((240, 240), dtype=np.float32)
    matte[:216, :] = 0.20
    matte[80:160, 80:160] = 1.0

    output, _summary = _blur_witness_pixels(image, matte, (), _options())

    soft_region = np.s_[10:70, 10:230]
    before = float(cv2.Laplacian(image[soft_region], cv2.CV_32F).var())
    after = float(cv2.Laplacian(output[soft_region], cv2.CV_32F).var())
    unchanged = np.mean(np.all(output[soft_region] == image[soft_region], axis=2))
    assert after < before * 0.20
    assert unchanged < 0.10
    np.testing.assert_array_equal(
        output[80:160, 80:160],
        image[80:160, 80:160],
    )


def test_normalized_background_blur_prevents_foreground_color_bleed() -> None:
    image = np.full((161, 161, 3), 20, dtype=np.uint8)
    image[50:111, 50:111] = (0, 0, 255)
    matte = np.zeros((161, 161), dtype=np.float32)
    matte[50:111, 50:111] = 1.0
    options = _options(feather_radius_ratio=0.0, blur_kernel_cap=61)

    output, _summary = _blur_witness_pixels(image, matte, (), options)

    np.testing.assert_array_equal(output[80, 80], image[80, 80])
    # A conventional whole-image Gaussian would introduce red around this edge.
    assert int(output[80, 47, 2]) <= 22
    assert int(output[80, 47, 0]) <= 22


def test_soft_foreground_below_hard_threshold_is_excluded_from_blur_source() -> None:
    image = np.full((161, 161, 3), 20, dtype=np.uint8)
    image[50:111, 50:111] = (0, 0, 255)
    matte = np.zeros((161, 161), dtype=np.float32)
    matte[50:111, 50:111] = 0.20

    output, _summary = _blur_witness_pixels(
        image,
        matte,
        (),
        _options(feather_radius_ratio=0.0, blur_kernel_cap=61),
    )

    # Alpha 0.20 is a protected soft edge, not valid background evidence.
    assert int(output[80, 47, 2]) <= 22
    assert int(output[80, 47, 0]) <= 22


def test_card_margin_is_protected_on_both_sides_of_the_quad_boundary() -> None:
    image = _image()
    matte = _matte()
    matte[:, :] = 0.0
    matte[45:126, 90:151] = 1.0

    output, _summary = _blur_witness_pixels(image, matte, (CARD,), _options())

    # The configured 2% margin expands by roughly five pixels.
    np.testing.assert_array_equal(output[170, 72], image[170, 72])
    assert not np.array_equal(output[170, 60], image[170, 60])


def test_blur_kernel_is_positive_odd_and_bounded_at_exact_cap(monkeypatch) -> None:
    seen: list[tuple[int, int]] = []
    original = cv2.GaussianBlur

    def capture(source, kernel, sigmaX, *args, **kwargs):
        seen.append(kernel)
        return original(source, kernel, sigmaX, *args, **kwargs)

    monkeypatch.setattr(cv2, "GaussianBlur", capture)

    _blur_witness_pixels(
        _image(),
        _matte(),
        (CARD,),
        _options(blur_sigma_ratio=0.25, blur_kernel_cap=31),
    )

    assert seen
    assert all(width == height == 31 for width, height in seen)
    assert all(width % 2 == 1 for width, _height in seen)


def test_normalized_blur_float_work_is_bounded_but_output_stays_original_size(
    monkeypatch,
) -> None:
    image = cv2.resize(_image(), (512, 512), interpolation=cv2.INTER_NEAREST)
    matte = np.zeros((512, 512), dtype=np.float32)
    matte[60:490, 120:390] = 1.0
    seen: list[tuple[int, int]] = []
    original = cv2.GaussianBlur

    def capture(source, kernel, sigmaX, *args, **kwargs):
        seen.append(source.shape[:2])
        return original(source, kernel, sigmaX, *args, **kwargs)

    monkeypatch.setattr(cv2, "GaussianBlur", capture)

    output, _summary = _blur_witness_pixels(
        image,
        matte,
        (),
        _options(blur_working_max_dimension=128),
    )

    assert seen and all(max(shape) <= 128 for shape in seen)
    assert output.shape == image.shape
    assert output.dtype == np.uint8
    assert output.flags.c_contiguous
    np.testing.assert_array_equal(output[200, 200], image[200, 200])
