"""Concrete wire documentation without changing established response payloads."""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Literal, get_type_hints

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field, create_model

from face_algorithm.portrait.contracts import PortraitResult
from face_algorithm.witness.contracts import WitnessBlurResult
from face_algorithm.schemas import (
    ImageVerificationResult, ModelHealth, ReasonCode, VideoVerificationResult,
)
from .errors import ApiReasonCode


class ApiErrorResponse(BaseModel):
    success: Literal[False]
    suggestion: Literal["REVIEW"]
    reason_codes: list[ReasonCode | ApiReasonCode]
    message: str


class ImageVerificationResponse(ImageVerificationResult):
    """照片一对一核验结果；未标定分数不构成身份判定。"""


class VideoVerificationResponse(VideoVerificationResult):
    """旧 quick/full 持续主体一致性结果，不等同于目标出现检测。"""


class LivenessResponse(BaseModel):
    status: Literal["live"]


class ReadinessResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    ready: bool
    models: list[ModelHealth]
    capabilities: dict[str, str]


class LegacyFaceBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    score: float


class LegacyDetectionResponse(BaseModel):
    success: bool
    message: str
    faces: list[LegacyFaceBox]
    processing_time: float = Field(ge=0, description="耗时，单位秒")


class LegacyFeatureResponse(BaseModel):
    success: bool
    message: str
    feature: list[float] | None = Field(min_length=1024, max_length=1024)
    feature_dim: Literal[1024] | None
    processing_time: float = Field(ge=0, description="耗时，单位秒")


def _derived_wire_model(name: str, contract: type) -> type[BaseModel]:
    hints = get_type_hints(contract)
    definitions = {
        field.name: (hints[field.name], ...)
        for field in fields(contract)
        if field.name != "image"
    }
    definitions["image_base64"] = (
        str | None,
        Field(default=None, description="仅成功生成派生图时出现；不带data URI前缀"),
    )
    return create_model(name, __config__=ConfigDict(extra="forbid"), **definitions)


PortraitResponse = _derived_wire_model("PortraitResponse", PortraitResult)
WitnessBlurResponse = _derived_wire_model("WitnessBlurResponse", WitnessBlurResult)


def install_public_openapi(app: FastAPI) -> None:
    """Attach schemas to installed routes; disabled capabilities stay absent."""

    def public_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version,
            description=(
                "内网算法服务。业务后端调用API，测试台不是业务接口。"
                "presence查询视频目标出现证据；quick/full仍检查持续主体一致性。"
                "未标定时不得将分数或success解释为身份核验通过。"
            ),
            routes=app.routes,
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})

        def ref(model: type[BaseModel]) -> dict[str, str]:
            definition = model.model_json_schema(ref_template="#/components/schemas/{model}")
            components.update(definition.pop("$defs", {}))
            components[model.__name__] = definition
            return {"$ref": f"#/components/schemas/{model.__name__}"}

        def response(model: type[BaseModel], description: str) -> dict[str, Any]:
            return {"description": description, "content": {"application/json": {"schema": ref(model)}}}

        result_models = {
            "/api/v2/portrait/generate": PortraitResponse,
            "/api/v2/witness/blur-background": WitnessBlurResponse,
            "/api/v2/verify/images": ImageVerificationResponse,
            "/api/v2/verify/video/quick": VideoVerificationResponse,
            "/api/v2/verify/video/full": VideoVerificationResponse,
            "/api/detect_face": LegacyDetectionResponse,
            "/api/extract_feature": LegacyFeatureResponse,
        }
        error_descriptions = {
            "400": "上传媒体、字段或参数不合法",
            "413": "上传或处理资源超过限制",
            "500": "内部推理或结果契约错误",
            "503": "服务停止、模型不可用或标定依赖不可用；排队超时为504",
            "504": "上传、解析及推理的总请求期限已到",
        }
        for path, operations in schema["paths"].items():
            if path.startswith("/api/") and "post" in operations:
                operation = operations["post"]
                responses = operation["responses"]
                responses.pop("422", None)
                for code, description in error_descriptions.items():
                    responses[code] = response(ApiErrorResponse, description)
                if path in result_models:
                    responses["200"] = response(result_models[path], "算法结果；请检查业务状态及原因码")
                if path == "/api/extract_feature":
                    schema["components"].setdefault("securitySchemes", {})["InternalToken"] = {
                        "type": "apiKey", "in": "header", "name": "X-Internal-Token",
                        "description": "仅允许已授权内部来源；服务端另校验来源CIDR。默认关闭此接口。",
                    }
                    operation["security"] = [{"InternalToken": []}]
                    responses["403"] = response(ApiErrorResponse, "未授权Token或来源地址")
            elif path in {"/health", "/health/ready", "/health/live"}:
                model = LivenessResponse if path.endswith("/live") else ReadinessResponse
                operations["get"]["responses"]["200"] = response(model, "存活或能力就绪状态")
                if model is ReadinessResponse:
                    operations["get"]["responses"]["503"] = response(model, "尚未就绪")
        app.openapi_schema = schema
        return schema

    app.openapi = public_openapi
