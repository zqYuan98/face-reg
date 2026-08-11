"""Application factory for the private, single-worker algorithm service."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from face_algorithm.portrait.runtime import (
    PortraitMattingDependencyError,
    PortraitMattingDisabledError,
)
from face_algorithm.runtime.calibration import CalibrationLoadError
from face_algorithm.runtime.config import AlgorithmConfig, load_config
from face_algorithm.runtime.model_registry import ModelRegistryError
from face_algorithm.schemas import ReasonCode

from .body_limit import RequestBodyLimitMiddleware, multipart_body_limits
from .deadline import RequestDeadlineMiddleware, algorithm_route_deadlines
from .errors import ApiError, error_payload
from .execution import AlgorithmExecutor
from .routes.legacy import create_legacy_router
from .routes.v2 import create_v2_router
from .security import LegacyAccessMiddleware
from .services import AlgorithmDeadlineExceeded, DefaultAlgorithmServices


logger = logging.getLogger("face_algorithm.api")
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _legacy_token(config: AlgorithmConfig, explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit
    if config.legacy.token is None:
        return None
    return config.legacy.token.get_secret_value()


def _validate_origins(origins: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(origin.strip().rstrip("/") for origin in origins if origin.strip())
    if any(origin == "*" or not origin.startswith(("https://", "http://")) for origin in normalized):
        raise ValueError("CORS origins must be explicit HTTP(S) origins")
    return normalized


def create_app(
    *,
    config: AlgorithmConfig,
    services: Any,
    legacy_token: str | None = None,
    allowed_origins: Iterable[str] = (),
) -> FastAPI:
    """Create an app without importing or loading heavyweight models."""

    if not isinstance(config, AlgorithmConfig):
        raise TypeError("config must be a validated AlgorithmConfig")
    token = _legacy_token(config, legacy_token)
    origins = _validate_origins(allowed_origins)
    executor = AlgorithmExecutor()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.startup_error = None
        try:
            await asyncio.to_thread(services.start)
        except Exception as error:
            app.state.startup_error = type(error).__name__
            logger.error("algorithm startup failed: %s", type(error).__name__)
        try:
            yield
        finally:
            try:
                await asyncio.to_thread(executor.close)
            finally:
                await asyncio.to_thread(services.close)

    app = FastAPI(
        title="Private Face Algorithm API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.config = config
    app.state.services = services
    app.state.algorithm_executor = executor

    app.add_middleware(
        RequestBodyLimitMiddleware,
        limits=multipart_body_limits(
            max_image_bytes=config.resources.max_image_bytes,
            max_video_bytes=config.resources.max_video_bytes,
        ),
    )
    if config.legacy.extract_feature_enabled:
        if not token:
            raise ValueError("legacy token is required when extract_feature is enabled")
        if len(token.encode("utf-8")) < 32:
            raise ValueError("legacy token must contain at least 32 UTF-8 bytes")
        app.add_middleware(
            LegacyAccessMiddleware,
            expected_token=token,
            allowed_client_cidrs=config.legacy.allowed_client_cidrs,
            trusted_proxy_cidrs=config.legacy.trusted_proxy_cidrs,
        )

    # Added after auth/body guards so this is the outer ingress clock for every
    # algorithm route, including legacy token verification and multipart parsing.
    app.add_middleware(
        RequestDeadlineMiddleware,
        deadlines=algorithm_route_deadlines(
            image_seconds=config.route_deadlines.image_seconds,
            video_quick_seconds=config.route_deadlines.video_quick_seconds,
            video_full_seconds=config.route_deadlines.video_full_seconds,
        ),
    )

    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Internal-Token", "X-Request-ID"],
            max_age=600,
        )

    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=error_payload(error.reason_code, error.public_message),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=error_payload(ReasonCode.INVALID_IMAGE, "invalid request fields"),
        )

    @app.exception_handler(ModelRegistryError)
    async def handle_model_error(
        _request: Request,
        _error: ModelRegistryError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=error_payload(ReasonCode.MODEL_UNAVAILABLE, "required model is unavailable"),
        )

    @app.exception_handler(PortraitMattingDisabledError)
    @app.exception_handler(PortraitMattingDependencyError)
    async def handle_portrait_model_error(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=error_payload(ReasonCode.MODEL_UNAVAILABLE, "portrait model is unavailable"),
        )

    @app.exception_handler(AlgorithmDeadlineExceeded)
    async def handle_algorithm_deadline(
        _request: Request,
        _error: AlgorithmDeadlineExceeded,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=504,
            content=error_payload(ReasonCode.ALGORITHM_TIMEOUT, "algorithm deadline exceeded"),
        )

    @app.exception_handler(CalibrationLoadError)
    async def handle_calibration_error(
        _request: Request,
        _error: CalibrationLoadError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=error_payload(
                ReasonCode.CALIBRATION_REQUIRED,
                "signed calibration is unavailable",
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, error: Exception) -> JSONResponse:
        logger.error("unclassified algorithm API failure: %s", type(error).__name__)
        return JSONResponse(
            status_code=500,
            content=error_payload(ReasonCode.INFERENCE_ERROR, "algorithm request failed"),
        )

    @app.get("/health/live", tags=["health"])
    async def health_live() -> dict[str, str]:
        return {"status": "live"}

    def readiness_response() -> JSONResponse:
        try:
            payload = dict(services.readiness())
        except Exception:
            payload = {"status": "unhealthy", "ready": False, "models": [], "capabilities": {}}
        if getattr(app.state, "startup_error", None):
            payload.update(status="unhealthy", ready=False)
        return JSONResponse(status_code=200 if payload.get("ready") else 503, content=payload)

    @app.get("/health/ready", tags=["health"])
    async def health_ready() -> JSONResponse:
        return readiness_response()

    @app.get("/health", tags=["health"], operation_id="compatibility_health")
    async def compatibility_health() -> JSONResponse:
        return readiness_response()

    app.include_router(create_v2_router(config=config, services=services, executor=executor))
    app.include_router(
        create_legacy_router(
            config=config,
            services=services,
            executor=executor,
            legacy_token=token,
        )
    )
    return app


def create_default_app() -> FastAPI:
    config = load_config()
    services = DefaultAlgorithmServices.from_config(
        config=config,
        project_root=_PROJECT_ROOT,
    )
    origins = tuple(
        item for item in os.environ.get("FACE_ALGO_CORS_ORIGINS", "").split(",") if item
    )
    return create_app(
        config=config,
        services=services,
        # An empty explicit value prevents secrets embedded in YAML from being
        # accepted by the production factory. Tests may still inject directly.
        legacy_token=os.environ.get("FACE_ALGO_LEGACY_TOKEN", ""),
        allowed_origins=origins,
    )
