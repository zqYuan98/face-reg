"""Bounded strict-JSON 1:N search without a stored database or image inference."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.requests import ClientDisconnect

from face_algorithm.core.matcher import EmbeddingMatchError
from face_algorithm.core.search import rank_embeddings
from face_algorithm.schemas import ReasonCode

from .deadline import request_deadline
from .errors import ApiError, error_payload
from .feature_comparison import (
    FeatureComparisonErrorResponse,
    FeatureVector,
    _reject_constant,
    _unique_object,
    require_float32_numbers,
)
from .search_execution import SearchExecution


FEATURE_SEARCH_PATH = "/api/search_feature"
FEATURE_SEARCH_MAX_BYTES = 32 * 1024 * 1024
FEATURE_SEARCH_DEADLINE_SECONDS = 5.0
FEATURE_SEARCH_MAX_CANDIDATES = 1000
logger = logging.getLogger("face_algorithm.api.search")


class FeatureSearchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    id: str = Field(min_length=1, max_length=128)
    feature: FeatureVector = Field(repr=False)

    @field_validator("id")
    @classmethod
    def require_nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("candidate ID must not be blank")
        # Reject unpaired JSON Unicode surrogates before response serialization.
        value.encode("utf-8", errors="strict")
        return value

    @field_validator("feature", mode="before")
    @classmethod
    def require_numbers(cls, value: Any) -> Any:
        return require_float32_numbers(value)


class FeatureSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    probe_feature: FeatureVector = Field(repr=False)
    probe_version: Literal["recognizer_v1_1024"]
    reference_version: Literal["recognizer_v1_1024"]
    candidates: list[FeatureSearchCandidate] = Field(min_length=1, max_length=FEATURE_SEARCH_MAX_CANDIDATES, repr=False)
    top_k: int = Field(default=1, ge=1, le=FEATURE_SEARCH_MAX_CANDIDATES)

    @field_validator("probe_feature", mode="before")
    @classmethod
    def require_numbers(cls, value: Any) -> Any:
        return require_float32_numbers(value)

    @model_validator(mode="after")
    def require_unique_ids_and_top_k(self) -> "FeatureSearchRequest":
        if self.top_k > len(self.candidates):
            raise ValueError("top_k exceeds candidate count")
        if len({candidate.id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("candidate IDs must be unique")
        return self


class FeatureSearchMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: str
    similarity_score: float = Field(ge=-1, le=1, allow_inf_nan=False)


class FeatureSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    success: Literal[True] = True
    matches: list[FeatureSearchMatch]
    candidate_count: int = Field(ge=1, le=FEATURE_SEARCH_MAX_CANDIDATES)
    top_k: int = Field(ge=1, le=FEATURE_SEARCH_MAX_CANDIDATES)
    feature_dimension: Literal[1024] = 1024
    feature_version: Literal["recognizer_v1_1024"] = "recognizer_v1_1024"
    matcher_version: Literal["l2_dot_v1"] = "l2_dot_v1"
    processing_time_ms: float = Field(ge=0, allow_inf_nan=False)


def _require_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("feature search deadline exceeded")


def _parse_and_search(body: bytes, *, deadline: float) -> tuple[int, dict[str, object]]:
    """Run only in the child: decode, parse, validate, normalize and rank."""
    try:
        _require_deadline(deadline)
        if len(body) > FEATURE_SEARCH_MAX_BYTES:
            return 413, error_payload(ReasonCode.FEATURE_PAYLOAD_TOO_LARGE, "feature request exceeds the configured limit")
        try:
            payload = json.loads(
                body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant,
            )
            request = FeatureSearchRequest.model_validate(payload)
        except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
            return 400, error_payload(ReasonCode.INVALID_FEATURE, "invalid feature request")
        _require_deadline(deadline)
        try:
            ranked = rank_embeddings(
                request.probe_feature,
                [candidate.feature for candidate in request.candidates],
                probe_version=request.probe_version,
                reference_version=request.reference_version,
                top_k=request.top_k,
                deadline=deadline,
            )
        except EmbeddingMatchError:
            return 400, error_payload(ReasonCode.INVALID_FEATURE, "invalid feature request")
        _require_deadline(deadline)
        return 200, {
            "success": True,
            "matches": [{"id": request.candidates[index].id, "similarity_score": score} for index, score in ranked],
            "candidate_count": len(request.candidates),
            "top_k": request.top_k,
            "feature_dimension": 1024,
            "feature_version": "recognizer_v1_1024",
            "matcher_version": "l2_dot_v1",
        }
    except TimeoutError:
        return 504, error_payload(ReasonCode.ALGORITHM_TIMEOUT, "feature request deadline exceeded")
    except Exception as error:
        logger.error("feature search failed: %s", type(error).__name__)
        return 500, error_payload(ReasonCode.INFERENCE_ERROR, "feature search failed")


async def _read_body(request: Request, *, deadline: float) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise ApiError(400, ReasonCode.INVALID_FEATURE, "application/json is required")
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            length = int(declared_length)
        except ValueError:
            length = FEATURE_SEARCH_MAX_BYTES + 1
        if not 0 <= length <= FEATURE_SEARCH_MAX_BYTES:
            raise ApiError(413, ReasonCode.FEATURE_PAYLOAD_TOO_LARGE, "feature request exceeds the configured limit")
    body = bytearray()

    async def read() -> None:
        async for chunk in request.stream():
            _require_deadline(deadline)
            if len(body) + len(chunk) > FEATURE_SEARCH_MAX_BYTES:
                raise ApiError(413, ReasonCode.FEATURE_PAYLOAD_TOO_LARGE, "feature request exceeds the configured limit")
            body.extend(chunk)

    await asyncio.wait_for(read(), timeout=max(0.0, deadline - time.monotonic()))
    _require_deadline(deadline)
    return bytes(body)


def create_feature_search_router(*, execution: SearchExecution | None = None) -> APIRouter:
    runtime = execution if execution is not None else SearchExecution()

    @asynccontextmanager
    async def lifespan(_application: Any):
        await runtime.start()
        try:
            yield
        finally:
            runtime.close()

    router = APIRouter(tags=["feature-search"], lifespan=lifespan)
    request_schema = FeatureSearchRequest.model_json_schema()
    # The manual requestBody must have self-contained schemas: Pydantic's
    # default #/$defs/... references do not resolve at the OpenAPI document root.
    request_schema.pop("$defs", None)
    request_schema["properties"]["candidates"]["items"] = FeatureSearchCandidate.model_json_schema()

    @router.post(
        FEATURE_SEARCH_PATH,
        operation_id="search_feature_vectors",
        summary="从 1–1000 个同协议特征中返回分数最高的候选",
        description=(
            "UTF-8 JSON 最多 32 MiB，绝对请求期限 5 秒。候选 ID 唯一且为 1–128 字符非空白字符串。"
            "所有特征必须为有限、非零的 1024 维数值向量，并声明 recognizer_v1_1024。"
            "top_k 默认为 1，范围 1–候选数量。按归一化余弦分数降序，同分保持输入顺序；负分合法。"
            "任何无效候选使整批失败。每个服务进程只有一个独立搜索槽，无排队；忙时立即返回 429。"
            "不保存特征、不设置身份阈值、不加载图片模型，也不占图片推理槽。"
        ),
        response_model=FeatureSearchResponse,
        responses={
            400: {"model": FeatureComparisonErrorResponse, "description": "JSON、特征、ID、top_k 或版本无效"},
            413: {"model": FeatureComparisonErrorResponse, "description": "JSON 请求体超过 32 MiB"},
            429: {"model": FeatureComparisonErrorResponse, "description": "搜索槽正在使用"},
            500: {"model": FeatureComparisonErrorResponse, "description": "搜索执行失败"},
            503: {"model": FeatureComparisonErrorResponse, "description": "搜索工作进程不可用"},
            504: {"model": FeatureComparisonErrorResponse, "description": "请求超过 5 秒截止时间"},
        },
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": request_schema},
        }}},
    )
    async def search_features(request: Request) -> JSONResponse:
        started = time.monotonic()
        deadline = min(
            request_deadline(request, fallback_seconds=FEATURE_SEARCH_DEADLINE_SECONDS),
            started + FEATURE_SEARCH_DEADLINE_SECONDS,
        )
        admission = None
        try:
            _require_deadline(deadline)
            admission = runtime.reserve()
            body = await _read_body(request, deadline=deadline)
            _require_deadline(deadline)
            future = runtime.submit(admission, body, deadline=deadline)
            status_code, result = await runtime.wait(future, timeout=deadline - time.monotonic())
            _require_deadline(deadline)
            if status_code == 200:
                result["processing_time_ms"] = max(0.0, (time.monotonic() - started) * 1000.0)
            response = JSONResponse(status_code=status_code, content=result)
            _require_deadline(deadline)
            return response
        except (asyncio.TimeoutError, TimeoutError):
            return JSONResponse(status_code=504, content=error_payload(ReasonCode.ALGORITHM_TIMEOUT, "feature request deadline exceeded"))
        except ClientDisconnect:
            return JSONResponse(status_code=400, content=error_payload(ReasonCode.INVALID_FEATURE, "invalid feature request"))
        except ApiError as error:
            return JSONResponse(status_code=error.status_code, content=error_payload(error.reason_code, error.public_message))
        except Exception as error:
            logger.error("feature search request failed: %s", type(error).__name__)
            return JSONResponse(status_code=500, content=error_payload(ReasonCode.INFERENCE_ERROR, "feature search failed"))
        finally:
            if admission is not None:
                runtime.release_unsubmitted(admission)

    return router
