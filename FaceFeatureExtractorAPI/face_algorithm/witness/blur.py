"""Subject/card mask selection and foreground-safe background blur."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from face_algorithm.schemas import CardRegion, FaceBox, ReasonCode
from face_algorithm.witness.contracts import WitnessBlurSummary, WitnessMattingQuality

__all__: list[str] = []


class _WitnessBlurError(ValueError):
    def __init__(self, reason_code: ReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.value)


def _face_slices(face: FaceBox, shape: tuple[int, int]) -> tuple[slice, slice]:
    height, width = shape
    x1 = max(0, min(width, int(np.floor(face.x1))))
    y1 = max(0, min(height, int(np.floor(face.y1))))
    x2 = max(0, min(width, int(np.ceil(face.x2))))
    y2 = max(0, min(height, int(np.ceil(face.y2))))
    if x2 <= x1 or y2 <= y1:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
    return slice(y1, y2), slice(x1, x2)


def _select_witness_subject_matte(
    matte: np.ndarray,
    face: FaceBox,
    options: Any,
) -> tuple[np.ndarray, WitnessMattingQuality]:
    if not (
        isinstance(matte, np.ndarray)
        and matte.dtype == np.float32
        and matte.ndim == 2
        and matte.size > 0
        and matte.flags.c_contiguous
        and np.all(np.isfinite(matte))
        and float(matte.min()) >= 0.0
        and float(matte.max()) <= 1.0
    ):
        raise TypeError("matting output violates the float32 alpha contract")
    height, width = matte.shape
    face_y, face_x = _face_slices(face, (height, width))
    hard = np.asarray(matte >= float(options.mask_threshold), np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(hard, connectivity=8)
    selected_label = 0
    selected_key = (0, 0)
    for label in range(1, count):
        overlap = int(np.count_nonzero(labels[face_y, face_x] == label))
        area = int(stats[label, cv2.CC_STAT_AREA])
        if overlap > 0 and (overlap, area) > selected_key:
            selected_key = (overlap, area)
            selected_label = label
    if selected_label == 0:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
    core = np.asarray(labels == selected_label, np.uint8)
    area_ratio = float(np.count_nonzero(core)) / float(height * width)
    if area_ratio < float(options.min_subject_area_ratio):
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)

    soft_binary = np.asarray(matte >= float(options.subject_edge_alpha), np.uint8)
    soft_count, soft_labels, _soft_stats, _ = cv2.connectedComponentsWithStats(
        soft_binary, connectivity=8
    )
    overlapping = soft_labels[core > 0]
    counts = np.bincount(overlapping, minlength=soft_count)
    if counts.size <= 1:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
    counts[0] = 0
    soft_label = int(np.argmax(counts))
    if counts[soft_label] <= 0:
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
    soft_component = np.asarray(soft_labels == soft_label, np.uint8)
    soft_area_ratio = float(np.count_nonzero(soft_component)) / float(height * width)
    if soft_area_ratio > float(options.max_subject_area_ratio):
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
    cleaned = np.where(soft_component > 0, matte, np.float32(0.0)).astype(
        np.float32, copy=False
    )
    cleaned = np.ascontiguousarray(cleaned)
    face_values = cleaned[face_y, face_x]
    face_mean = float(np.mean(face_values, dtype=np.float64))
    face_coverage = float(
        np.count_nonzero(face_values >= float(options.mask_threshold))
    ) / float(face_values.size)
    if (
        face_mean < float(options.min_face_alpha_mean)
        or face_coverage < float(options.min_face_alpha_coverage)
    ):
        raise _WitnessBlurError(ReasonCode.MATTING_LOW_CONFIDENCE)
    return cleaned, WitnessMattingQuality(
        face_mean=face_mean,
        face_coverage=face_coverage,
        subject_area_ratio=area_ratio,
    )


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    return np.where(
        values <= np.float32(0.04045),
        values / np.float32(12.92),
        ((values + np.float32(0.055)) / np.float32(1.055)) ** np.float32(2.4),
    )


_SRGB_TO_LINEAR_LUT = np.ascontiguousarray(
    _srgb_to_linear(np.arange(256, dtype=np.float32) / np.float32(255.0))
)


def _linear_to_srgb(values: np.ndarray) -> np.ndarray:
    return np.where(
        values <= np.float32(0.0031308),
        values * np.float32(12.92),
        np.float32(1.055) * np.power(values, np.float32(1.0 / 2.4))
        - np.float32(0.055),
    )


def _card_mask(
    shape: tuple[int, int], cards: tuple[CardRegion, ...], margin_ratio: float
) -> np.ndarray:
    height, width = shape
    mask = np.zeros((height, width), np.uint8)
    for card in cards:
        quad = np.asarray([(point.x, point.y) for point in card.corners], np.float32)
        center = np.mean(quad, axis=0)
        expanded = center + (quad - center) * np.float32(1.0 + 2.0 * margin_ratio)
        expanded[:, 0] = np.clip(expanded[:, 0], 0.0, width - 1.0)
        expanded[:, 1] = np.clip(expanded[:, 1], 0.0, height - 1.0)
        cv2.fillConvexPoly(mask, np.rint(expanded).astype(np.int32), 1)
    return mask


def _blur_witness_pixels(
    image: np.ndarray,
    subject_matte: np.ndarray,
    cards: tuple[CardRegion, ...],
    options: Any,
) -> tuple[np.ndarray, WitnessBlurSummary]:
    if not (
        isinstance(image, np.ndarray)
        and image.dtype == np.uint8
        and image.ndim == 3
        and image.shape[2] == 3
        and image.flags.c_contiguous
        and isinstance(subject_matte, np.ndarray)
        and subject_matte.dtype == np.float32
        and subject_matte.shape == image.shape[:2]
        and subject_matte.flags.c_contiguous
        and np.all(np.isfinite(subject_matte))
        and float(subject_matte.min()) >= 0.0
        and float(subject_matte.max()) <= 1.0
    ):
        raise TypeError("witness blur input contract violated")
    height, width = image.shape[:2]
    card_core = _card_mask(
        (height, width), cards, float(options.card_margin_ratio)
    )
    subject_core = np.asarray(
        subject_matte >= float(options.mask_threshold), np.uint8
    )
    hard_core = np.maximum(subject_core, card_core)
    del subject_core
    protect = subject_matte.copy()
    protect[card_core > 0] = np.float32(1.0)
    feather_radius = int(
        np.ceil(min(height, width) * float(options.feather_radius_ratio))
    )
    if feather_radius > 0 and np.any(card_core):
        outside_distance = cv2.distanceTransform(1 - card_core, cv2.DIST_L2, 3)
        feather = np.clip(
            1.0 - outside_distance / float(feather_radius + 1), 0.0, 1.0
        ).astype(np.float32)
        np.maximum(protect, feather, out=protect)
        del outside_distance, feather
    np.clip(protect, 0.0, 1.0, out=protect)

    sigma = max(0.1, min(height, width) * float(options.blur_sigma_ratio))
    working_scale = min(
        1.0,
        float(options.blur_working_max_dimension) / float(max(height, width)),
    )
    working_width = max(1, int(round(width * working_scale)))
    working_height = max(1, int(round(height * working_scale)))
    working_sigma = max(0.1, sigma * working_scale)
    desired = int(2 * np.ceil(3.0 * working_sigma) + 1)
    cap = max(3, int(round(int(options.blur_kernel_cap) * working_scale)))
    if cap % 2 == 0:
        cap -= 1
    kernel_size = max(3, min(desired, cap))
    if kernel_size % 2 == 0:
        kernel_size -= 1
    blur_exclusion = np.maximum(
        np.asarray(subject_matte >= float(options.subject_edge_alpha), np.uint8),
        card_core,
    )
    background_weight = np.asarray(1 - blur_exclusion, np.uint8)
    del blur_exclusion
    background_weight_float = background_weight.astype(np.float32)
    if working_scale < 1.0:
        weight_working = cv2.resize(
            background_weight_float,
            (working_width, working_height),
            interpolation=cv2.INTER_AREA,
        )
    else:
        weight_working = background_weight_float
    del background_weight_float
    denominator = cv2.GaussianBlur(
        weight_working,
        (kernel_size, kernel_size),
        sigmaX=working_sigma,
        sigmaY=working_sigma,
        borderType=cv2.BORDER_REFLECT_101,
    )
    valid_working = denominator > np.float32(1e-6)
    if working_scale < 1.0:
        valid_full = cv2.resize(
            valid_working.astype(np.uint8),
            (width, height),
            interpolation=cv2.INTER_NEAREST,
        )
    else:
        valid_full = valid_working.astype(np.uint8)

    output = np.empty_like(image)
    hard_pixels = hard_core > 0
    for channel in range(3):
        weighted = np.take(_SRGB_TO_LINEAR_LUT, image[:, :, channel])
        np.multiply(weighted, background_weight, out=weighted)
        if working_scale < 1.0:
            numerator = cv2.resize(
                weighted,
                (working_width, working_height),
                interpolation=cv2.INTER_AREA,
            )
        else:
            numerator = weighted
        del weighted
        numerator = cv2.GaussianBlur(
            numerator,
            (kernel_size, kernel_size),
            sigmaX=working_sigma,
            sigmaY=working_sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
        np.divide(numerator, denominator, out=numerator, where=valid_working)
        numerator[~valid_working] = np.float32(0.0)
        if working_scale < 1.0:
            blurred = cv2.resize(
                numerator,
                (width, height),
                interpolation=cv2.INTER_LINEAR,
            )
        else:
            blurred = numerator
        del numerator
        source_linear = np.take(_SRGB_TO_LINEAR_LUT, image[:, :, channel])
        np.copyto(blurred, source_linear, where=valid_full == 0)
        np.subtract(source_linear, blurred, out=source_linear)
        np.multiply(source_linear, protect, out=source_linear)
        np.add(blurred, source_linear, out=blurred)
        np.clip(blurred, 0.0, 1.0, out=blurred)
        srgb = _linear_to_srgb(blurred)
        np.clip(srgb, 0.0, 1.0, out=srgb)
        np.multiply(srgb, np.float32(255.0), out=srgb)
        np.rint(srgb, out=srgb)
        output_channel = srgb.astype(np.uint8)
        output_channel[hard_pixels] = image[:, :, channel][hard_pixels]
        output[:, :, channel] = output_channel
        del blurred, source_linear, srgb, output_channel
    output = np.ascontiguousarray(output)
    return output, WitnessBlurSummary(
        protected_area_ratio=float(np.count_nonzero(hard_core)) / float(height * width),
        sigma_pixels=float(sigma),
        kernel_size=kernel_size,
    )
