"""Absolute request deadlines that begin before request-body parsing."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from face_algorithm.schemas import ReasonCode

from .errors import error_payload


_DEADLINE_STATE_KEY = "face_algorithm_deadline_monotonic"
FEATURE_COMPARISON_DEADLINE_SECONDS = 5.0


class RequestDeadlineMiddleware:
    """Bound upload, parsing, inference, and response creation to one deadline."""

    def __init__(self, app: Any, *, deadlines: Mapping[str, float]) -> None:
        self._app = app
        self._deadlines = {path: float(seconds) for path, seconds in deadlines.items()}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        seconds = self._deadlines.get(str(scope.get("path", "")))
        if seconds is None:
            await self._app(scope, receive, send)
            return

        deadline = time.monotonic() + seconds
        scope.setdefault("state", {})[_DEADLINE_STATE_KEY] = deadline
        timed_out = False
        response_started = False

        async def deadline_receive() -> dict[str, Any]:
            nonlocal timed_out
            if timed_out:
                return {"type": "http.disconnect"}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                return {"type": "http.disconnect"}
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                timed_out = True
                return {"type": "http.disconnect"}
            if time.monotonic() >= deadline:
                timed_out = True
                return {"type": "http.disconnect"}
            return message

        async def deadline_send(message: dict[str, Any]) -> None:
            nonlocal timed_out, response_started
            if not response_started and time.monotonic() >= deadline:
                timed_out = True
            if timed_out:
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, deadline_receive, deadline_send)
        except Exception:
            if not timed_out:
                raise
        if timed_out and not response_started:
            response = JSONResponse(
                status_code=504,
                content=error_payload(
                    ReasonCode.ALGORITHM_TIMEOUT,
                    "request deadline exceeded",
                ),
            )
            await response(scope, receive, send)


def request_deadline(request: Request, *, fallback_seconds: float) -> float:
    """Read the ingress deadline; retain a safe fallback for standalone routers."""

    value = request.scope.get("state", {}).get(_DEADLINE_STATE_KEY)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        deadline = float(value)
        if math.isfinite(deadline):
            return deadline
    return time.monotonic() + float(fallback_seconds)


def algorithm_route_deadlines(
    *,
    image_seconds: float,
    video_quick_seconds: float,
    video_full_seconds: float,
) -> dict[str, float]:
    return {
        "/api/v2/features/compare": FEATURE_COMPARISON_DEADLINE_SECONDS,
        "/api/v2/faces/analyze": image_seconds,
        "/api/v2/liveness/rgb": image_seconds,
        "/api/v2/portrait/generate": image_seconds,
        "/api/v2/witness/blur-background": image_seconds,
        "/api/v2/verify/images": image_seconds,
        "/api/v2/verify/video/quick": video_quick_seconds,
        "/api/v2/verify/video/full": video_full_seconds,
        "/api/v2/verify/video/presence": video_full_seconds,
        "/api/detect_face": image_seconds,
        "/api/extract_feature": image_seconds,
    }
