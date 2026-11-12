"""Bounded JSON comparison of caller-supplied vectors, without model inference."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Annotated, Any, Literal

import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.requests import ClientDisconnect

from face_algorithm.core.matcher import EmbeddingMatchError, match_embeddings
from face_algorithm.schemas import FEATURE_DIMENSIONS, FeatureVersion, ReasonCode

from .body_limit import FEATURE_COMPARISON_MAX_BYTES
from .deadline import FEATURE_COMPARISON_DEADLINE_SECONDS, request_deadline
from .errors import ApiError, error_payload


V2_FEATURE_COMPARISON_PATH = "/api/v2/features/compare"
LEGACY_FEATURE_COMPARISON_PATH = "/api/compare_feature"
_FLOAT32_MAX = float(np.finfo(np.float32).max)
logger = logging.getLogger("face_algorithm.api.features")

FeatureVector = Annotated[
    list[Annotated[float, Field(strict=True, allow_inf_nan=False)]],
    Field(min_length=min(FEATURE_DIMENSIONS.values()), max_length=max(FEATURE_DIMENSIONS.values())),
]
VersionName = Literal["recognizer_v1_1024", "arcface_r50_512"]


def require_float32_numbers(value: Any) -> Any:
    """Strict JSON-vector validation shared by comparison and search. The length must be one of the
    supported feature dimensions; callers check it against the declared version."""
    if type(value) is not list or len(value) not in FEATURE_DIMENSIONS.values():
        raise ValueError("feature has an unsupported length")
    for item in value:
        # Do not coerce JSON booleans, strings, objects, or nested arrays.
        if type(item) not in (int, float):
            raise ValueError("feature values must be JSON numbers")
        try:
            number = float(item)
        except (OverflowError, ValueError):
            raise ValueError("feature value is outside float32 range") from None
        if not math.isfinite(number) or abs(number) > _FLOAT32_MAX:
            raise ValueError("feature values must be finite float32 numbers")
    return value


class FeatureComparisonRequest(BaseModel):
    """Both vectors must declare the supported, shared extraction protocol."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True,
    )

    reference_feature: FeatureVector = Field(repr=False)
    probe_feature: FeatureVector = Field(repr=False)
    reference_version: VersionName
    probe_version: VersionName

    @field_validator("reference_feature", "probe_feature", mode="before")
    @classmethod
    def require_float32_numbers(cls, value: Any) -> Any:
        return require_float32_numbers(value)

    @model_validator(mode="after")
    def require_matching_versions(self):
        if self.reference_version != self.probe_version:
            raise ValueError("reference and probe feature versions differ")
        dimension = FEATURE_DIMENSIONS[FeatureVersion(self.reference_version)]
        if len(self.reference_feature) != dimension or len(self.probe_feature) != dimension:
            raise ValueError("feature length does not match its version")
        return self


class FeatureComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    success: Literal[True] = True
    similarity_score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    feature_dimension: Literal[1024, 512] = 1024
    feature_version: VersionName = "recognizer_v1_1024"
    matcher_version: Literal["l2_dot_v1"] = "l2_dot_v1"
    processing_time_ms: float = Field(ge=0, allow_inf_nan=False)


class FeatureComparisonErrorResponse(BaseModel):
    success: Literal[False]
    suggestion: Literal["REVIEW"]
    reason_codes: list[ReasonCode]
    message: str


def _require_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ApiError(504, ReasonCode.ALGORITHM_TIMEOUT, "feature request deadline exceeded")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON value")


async def _read_request(request: Request, *, deadline: float) -> FeatureComparisonRequest:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise ApiError(400, ReasonCode.INVALID_FEATURE, "application/json is required")
    _require_deadline(deadline)
    body = bytearray()

    async def read_body() -> None:
        async for chunk in request.stream():
            if len(body) + len(chunk) > FEATURE_COMPARISON_MAX_BYTES:
                raise ApiError(
                    413, ReasonCode.FEATURE_PAYLOAD_TOO_LARGE,
                    "feature request exceeds the configured limit",
                )
            body.extend(chunk)

    try:
        # The ASGI guards run before this reader; keep the same limits when the
        # router is mounted independently, too. No unbounded request.body().
        await asyncio.wait_for(read_body(), timeout=max(0.0, deadline - time.monotonic()))
        _require_deadline(deadline)
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        result = FeatureComparisonRequest.model_validate(payload)
        _require_deadline(deadline)
        return result
    except (asyncio.TimeoutError, TimeoutError):
        raise ApiError(504, ReasonCode.ALGORITHM_TIMEOUT, "feature request deadline exceeded") from None
    except (ClientDisconnect, UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
        # Validation errors can carry complete input arrays. Never expose or log
        # their text, error list, or exception context.
        raise ApiError(400, ReasonCode.INVALID_FEATURE, "invalid feature request") from None


def create_feature_comparison_router(*, path: str) -> APIRouter:
    """Mount the same strict contract on either new or legacy HTTP service."""

    if path not in {V2_FEATURE_COMPARISON_PATH, LEGACY_FEATURE_COMPARISON_PATH}:
        raise ValueError("unsupported feature comparison route")
    router = APIRouter(tags=["feature-comparison"])

    @router.post(
        path,
        operation_id="compare_feature_vectors" if path == V2_FEATURE_COMPARISON_PATH else "legacy_compare_feature_vectors",
        summary="比较两个同版本的人脸特征",
        description=(
            "JSON 体最多 128 KiB，总请求期限 5 秒。两侧必须声明同一特征版本：recognizer_v1_1024（1024 维）"
            "或 arcface_r50_512（512 维），向量为有限、非零数值。返回归一化余弦分数，不返回输入向量、身份通过结论或阈值。"
            "调用方负责确认特征实际来源兼容；此接口不加载模型、不评估图像质量或活体，且不占图像推理槽。"
        ),
        response_model=FeatureComparisonResponse,
        responses={
            400: {"model": FeatureComparisonErrorResponse, "description": "JSON、特征或版本无效"},
            413: {"model": FeatureComparisonErrorResponse, "description": "JSON 请求体超过 128 KiB"},
            500: {"model": FeatureComparisonErrorResponse, "description": "比较执行失败"},
            504: {"model": FeatureComparisonErrorResponse, "description": "请求超过 5 秒截止时间"},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": FeatureComparisonRequest.model_json_schema()},
                },
            },
        },
    )
    async def compare_features(request: Request) -> FeatureComparisonResponse | JSONResponse:
        started = time.monotonic()
        deadline = min(
            request_deadline(request, fallback_seconds=FEATURE_COMPARISON_DEADLINE_SECONDS),
            started + FEATURE_COMPARISON_DEADLINE_SECONDS,
        )
        try:
            payload = await _read_request(request, deadline=deadline)
            # Exactly two bounded vectors; the existing matcher owns numerical
            # normalization. No model registry, lazy runtime, or inference gate.
            reference = np.asarray(payload.reference_feature, dtype=np.float32)
            probe = np.asarray(payload.probe_feature, dtype=np.float32)
            try:
                result = match_embeddings(
                    reference, probe,
                    reference_version=payload.reference_version,
                    probe_version=payload.probe_version,
                )
            except EmbeddingMatchError:
                raise ApiError(400, ReasonCode.INVALID_FEATURE, "invalid feature request") from None
            _require_deadline(deadline)
            return FeatureComparisonResponse(
                similarity_score=result.score,
                feature_dimension=result.feature_dimension,
                feature_version=result.feature_version.value,
                processing_time_ms=max(0.0, (time.monotonic() - started) * 1000.0),
            )
        except ApiError as error:
            return JSONResponse(
                status_code=error.status_code,
                content=error_payload(error.reason_code, error.public_message),
            )
        except Exception as error:
            logger.error("feature comparison failed: %s", type(error).__name__)
            return JSONResponse(
                status_code=500,
                content=error_payload(ReasonCode.INFERENCE_ERROR, "feature comparison failed"),
            )

    return router
