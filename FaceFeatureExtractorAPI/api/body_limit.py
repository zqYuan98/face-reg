"""ASGI-level aggregate request limits applied before multipart parsing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi.responses import JSONResponse

from face_algorithm.schemas import ReasonCode

from .errors import error_payload


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        limits: Mapping[str, tuple[int, ReasonCode]],
    ) -> None:
        self._app = app
        self._limits = dict(limits)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        policy = self._limits.get(str(scope.get("path", "")))
        if policy is None:
            await self._app(scope, receive, send)
            return
        limit, reason = policy
        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                declared = limit + 1
            if declared < 0 or declared > limit:
                await self._reject(scope, receive, send, reason)
                return

        consumed = 0
        response_started = False
        overflow = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal consumed, overflow
            if overflow:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    overflow = True
                    return {"type": "http.disconnect"}
            return message

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if overflow:
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, tracked_send)
        except Exception:
            if not overflow:
                raise
        if overflow:
            if response_started:
                return
            await self._reject(scope, receive, send, reason)

    @staticmethod
    async def _reject(
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        reason: ReasonCode,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content=error_payload(reason, "multipart request exceeds the configured limit"),
        )
        await response(scope, receive, send)


def multipart_body_limits(
    *,
    max_image_bytes: int,
    max_video_bytes: int,
) -> dict[str, tuple[int, ReasonCode]]:
    # Multipart metadata is bounded separately so ignored fields cannot create
    # an unbounded spool before a route sees its declared UploadFile objects.
    overhead = 64 * 1024
    image = max_image_bytes + overhead
    two_images = 2 * max_image_bytes + overhead
    video = max_image_bytes + max_video_bytes + overhead
    return {
        "/api/v2/portrait/generate": (image, ReasonCode.IMAGE_TOO_LARGE),
        "/api/v2/witness/blur-background": (image, ReasonCode.IMAGE_TOO_LARGE),
        "/api/v2/verify/images": (two_images, ReasonCode.IMAGE_TOO_LARGE),
        "/api/v2/verify/video/quick": (video, ReasonCode.VIDEO_TOO_LARGE),
        "/api/v2/verify/video/full": (video, ReasonCode.VIDEO_TOO_LARGE),
        "/api/detect_face": (image, ReasonCode.IMAGE_TOO_LARGE),
        "/api/extract_feature": (image, ReasonCode.IMAGE_TOO_LARGE),
    }
