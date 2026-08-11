"""Isolated compatibility routes, including the sole embedding exception."""

from __future__ import annotations

import math
import time
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from face_algorithm.runtime.config import AlgorithmConfig
from face_algorithm.schemas import FEATURE_DIMENSION, ReasonCode

from ..errors import ApiError
from ..deadline import request_deadline
from ..execution import AlgorithmExecutor
from ..media import read_image_upload
from ..security import require_legacy_access


def create_legacy_router(
    *,
    config: AlgorithmConfig,
    services: Any,
    executor: AlgorithmExecutor,
    legacy_token: str | None,
) -> APIRouter:
    router = APIRouter(tags=["legacy-compatibility"])
    resources = config.resources

    if config.legacy.detect_face_enabled:

        @router.post("/api/detect_face", operation_id="legacy_detect_face")
        async def detect_face(
            request: Request,
            image: UploadFile = File(...),
            expand_scale: Annotated[
                float,
                Form(ge=0.0, le=2.0, allow_inf_nan=False),
            ] = 0.0,
        ) -> dict[str, Any]:
            started = time.perf_counter()
            deadline = request_deadline(
                request,
                fallback_seconds=config.route_deadlines.image_seconds,
            )
            decoded = await read_image_upload(
                image,
                max_bytes=resources.max_image_bytes,
                max_pixels=resources.max_image_pixels,
                deadline_monotonic=deadline,
            )
            boxes = await executor.run(
                lambda: services.detect_faces(decoded),
                deadline_monotonic=deadline,
            )
            height, width = decoded.shape[:2]
            faces: list[dict[str, float]] = []
            for box in boxes or ():
                x1 = float(box.x1)
                y1 = float(box.y1)
                x2 = float(box.x2)
                y2 = float(box.y2)
                if expand_scale > 0:
                    box_width, box_height = x2 - x1, y2 - y1
                    center_x, center_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    x1 = center_x - box_width * (1.0 + expand_scale) / 2.0
                    x2 = center_x + box_width * (1.0 + expand_scale) / 2.0
                    y1 = center_y - box_height * (1.0 + expand_scale) / 2.0
                    y2 = center_y + box_height * (1.0 + expand_scale) / 2.0
                faces.append(
                    {
                        "x1": max(0.0, min(x1, float(width))),
                        "y1": max(0.0, min(y1, float(height))),
                        "x2": max(0.0, min(x2, float(width))),
                        "y2": max(0.0, min(y2, float(height))),
                        "score": float(box.score),
                    }
                )
            return {
                "success": bool(faces),
                "message": "Success" if faces else "No face detected",
                "faces": faces,
                "processing_time": max(0.0, time.perf_counter() - started),
            }

    if config.legacy.extract_feature_enabled:
        if not legacy_token:
            raise ValueError("legacy token is required when extract_feature is enabled")
        if len(legacy_token.encode("utf-8")) < 32:
            raise ValueError("legacy token must contain at least 32 UTF-8 bytes")

        def authorize_legacy_extract(request: Request) -> None:
            require_legacy_access(
                request,
                expected_token=legacy_token,
                allowed_client_cidrs=config.legacy.allowed_client_cidrs,
                trusted_proxy_cidrs=config.legacy.trusted_proxy_cidrs,
            )

        @router.post(
            "/api/extract_feature",
            operation_id="legacy_extract_feature",
            dependencies=[Depends(authorize_legacy_extract)],
        )
        async def extract_feature(
            request: Request,
            image: UploadFile = File(...),
        ) -> dict[str, Any]:
            started = time.perf_counter()
            deadline = request_deadline(
                request,
                fallback_seconds=config.route_deadlines.image_seconds,
            )
            decoded = await read_image_upload(
                image,
                max_bytes=resources.max_image_bytes,
                max_pixels=resources.max_image_pixels,
                deadline_monotonic=deadline,
            )
            feature = await executor.run(
                lambda: services.extract_feature(decoded),
                deadline_monotonic=deadline,
            )
            processing_time = max(0.0, time.perf_counter() - started)
            if feature is None:
                return {
                    "success": False,
                    "message": "No usable face detected",
                    "feature": None,
                    "feature_dim": None,
                    "processing_time": processing_time,
                }
            vector = np.asarray(feature)
            if (
                vector.dtype != np.float32
                or vector.shape != (FEATURE_DIMENSION,)
                or not np.all(np.isfinite(vector))
                or not math.isfinite(float(np.linalg.norm(vector.astype(np.float64))))
            ):
                raise ApiError(
                    500,
                    ReasonCode.FEATURE_VERSION_MISMATCH,
                    "legacy feature contract failed",
                )
            return {
                "success": True,
                "message": "Success",
                "feature": vector.tolist(),
                "feature_dim": FEATURE_DIMENSION,
                "processing_time": processing_time,
            }

    return router
