"""Concrete public DTO serialization for the HTTP boundary."""

from __future__ import annotations

import base64
import dataclasses
import math
from collections.abc import Mapping
from enum import Enum
from typing import Any, Literal

import cv2
import numpy as np
from face_algorithm.portrait.contracts import (
    PortraitMattingQuality,
    PortraitPlacement,
    PortraitResult,
)
from face_algorithm.schemas import (
    CardRegion,
    ImageVerificationResult,
    ReasonCode,
    VideoVerificationResult,
)

from .errors import ApiError

_WITNESS_JPEG_QUALITY_STEPS = (92, 88, 84, 80, 76)


def _contract_error() -> ApiError:
    return ApiError(
        500,
        ReasonCode.INFERENCE_ERROR,
        "algorithm result violates the public DTO contract",
    )


def _safe_value(value: Any, *, safe_dataclass_types: tuple[type[object], ...]) -> Any:
    if isinstance(value, Enum):
        return _safe_value(value.value, safe_dataclass_types=safe_dataclass_types)
    if isinstance(value, np.ndarray):
        raise _contract_error()
    if isinstance(value, np.generic):
        return _safe_value(value.item(), safe_dataclass_types=safe_dataclass_types)
    if type(value) is CardRegion:
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        if type(value) not in safe_dataclass_types:
            raise _contract_error()
        return {
            field.name: _safe_value(
                getattr(value, field.name),
                safe_dataclass_types=safe_dataclass_types,
            )
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(child, str)
            or not child
            for key, child in value.items()
        ):
            raise _contract_error()
        return dict(value)
    if isinstance(value, (tuple, list)):
        return [
            _safe_value(child, safe_dataclass_types=safe_dataclass_types)
            for child in value
        ]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _contract_error()
        return value
    raise _contract_error()


def image_verification_payload(result: object) -> dict[str, Any]:
    if type(result) is not ImageVerificationResult:
        raise _contract_error()
    try:
        raw = result.model_dump(mode="python", warnings="error")
        validated = ImageVerificationResult.model_validate(raw)
        return validated.model_dump(mode="json", warnings="error")
    except Exception:
        raise _contract_error() from None


def video_verification_payload(result: object) -> dict[str, Any]:
    if type(result) is not VideoVerificationResult:
        raise _contract_error()
    try:
        raw = result.model_dump(mode="python", warnings="error")
        validated = VideoVerificationResult.model_validate(raw)
        return validated.model_dump(mode="json", warnings="error")
    except Exception:
        raise _contract_error() from None


def _witness_contract() -> tuple[type[object], tuple[type[object], ...]]:
    try:
        from face_algorithm.witness import (
            WitnessBlurResult,
            WitnessBlurSummary,
            WitnessMattingQuality,
        )
    except ImportError:
        raise _contract_error() from None
    return WitnessBlurResult, (WitnessBlurSummary, WitnessMattingQuality)


def witness_contract_available() -> bool:
    try:
        _witness_contract()
    except ApiError:
        return False
    return True


def derived_image_payload(
    result: object,
    *,
    kind: Literal["portrait", "witness"],
    max_pixels: int,
    max_encoded_bytes: int,
) -> dict[str, Any]:
    if kind == "portrait":
        expected_type: type[object] = PortraitResult
        nested_types: tuple[type[object], ...] = (
            PortraitPlacement,
            PortraitMattingQuality,
        )
    else:
        expected_type, nested_types = _witness_contract()
    if type(result) is not expected_type or not dataclasses.is_dataclass(result):
        raise _contract_error()

    payload = {
        field.name: _safe_value(
            getattr(result, field.name),
            safe_dataclass_types=nested_types,
        )
        for field in dataclasses.fields(result)
        if field.name != "image"
    }
    image = getattr(result, "image", None)
    if image is None:
        return payload
    if not (
        isinstance(image, np.ndarray)
        and image.dtype == np.uint8
        and image.ndim == 3
        and image.shape[2] == 3
    ):
        raise _contract_error()
    if image.shape[0] * image.shape[1] > max_pixels:
        raise _contract_error()
    output_format = str(payload.get("output_format", "jpeg")).lower()
    if output_format == "png":
        extension = ".png"
        parameters: list[int] = []
        ok, encoded = cv2.imencode(extension, image, parameters)
        if not ok:
            raise ApiError(
                500,
                ReasonCode.INFERENCE_ERROR,
                "derived image encoding failed",
            )
        if encoded.size > max_encoded_bytes:
            raise _contract_error()
    elif kind == "witness":
        extension = ".jpg"
        encoded = None
        for quality in _WITNESS_JPEG_QUALITY_STEPS:
            ok, candidate = cv2.imencode(
                extension,
                image,
                [cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            if not ok:
                raise ApiError(
                    500,
                    ReasonCode.INFERENCE_ERROR,
                    "derived image encoding failed",
                )
            if candidate.size <= max_encoded_bytes:
                encoded = candidate
                break
            del candidate
        if encoded is None:
            raise ApiError(
                413,
                ReasonCode.IMAGE_TOO_LARGE,
                "derived witness image exceeds the encoded byte limit",
            )
    else:
        extension = ".jpg"
        quality = int(payload.get("jpeg_quality", 95))
        parameters = [cv2.IMWRITE_JPEG_QUALITY, max(1, min(quality, 100))]
        ok, encoded = cv2.imencode(extension, image, parameters)
        if not ok:
            raise ApiError(
                500,
                ReasonCode.INFERENCE_ERROR,
                "derived image encoding failed",
            )
        if encoded.size > max_encoded_bytes:
            raise _contract_error()
    payload["image_base64"] = base64.b64encode(bytes(encoded)).decode("ascii")
    return payload
