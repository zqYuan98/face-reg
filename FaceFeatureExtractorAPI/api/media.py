"""Bounded upload handling and image header preflight."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import cv2
import numpy as np
from fastapi import UploadFile

from face_algorithm.schemas import ReasonCode

from .errors import ApiError


_CHUNK_BYTES = 64 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF_MARKERS = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"})
_VIDEO_CHUNK_BYTES = 1024 * 1024
logger = logging.getLogger("face_algorithm.api")


@dataclass(slots=True)
class SpooledVideo:
    """Private video path whose cleanup can be transferred to a worker future."""

    path: Path
    _transferred: bool = False
    _cleaned: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def transfer_cleanup(self) -> Callable[[], None]:
        with self._lock:
            if self._transferred:
                raise RuntimeError("temporary video cleanup is already transferred")
            self._transferred = True
        return self.cleanup

    @property
    def cleanup_transferred(self) -> bool:
        with self._lock:
            return self._transferred

    def cleanup(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            for attempt in range(3):
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(0.01)
                else:
                    self._cleaned = True
                    return


async def _close_upload(upload: UploadFile) -> None:
    try:
        await upload.close()
    except Exception as error:
        logger.error("upload cleanup failed: %s", type(error).__name__)


def _flush_and_sync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _cleanup_untransferred(spooled: SpooledVideo) -> None:
    if spooled.cleanup_transferred:
        return
    try:
        spooled.cleanup()
    except OSError as error:
        logger.error("temporary video cleanup failed: %s", type(error).__name__)


def _check_deadline(deadline_monotonic: float) -> None:
    if time.monotonic() >= deadline_monotonic:
        raise ApiError(
            504,
            ReasonCode.ALGORITHM_TIMEOUT,
            "request media deadline exceeded",
        )


async def _read_limited(
    upload: UploadFile,
    *,
    max_bytes: int,
    deadline_monotonic: float,
) -> bytes:
    payload = bytearray()
    try:
        while True:
            _check_deadline(deadline_monotonic)
            remaining = max_bytes - len(payload)
            chunk = await upload.read(min(_CHUNK_BYTES, remaining + 1))
            _check_deadline(deadline_monotonic)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise ApiError(
                    413,
                    ReasonCode.IMAGE_TOO_LARGE,
                    "image upload exceeds the configured byte limit",
                )
    finally:
        await _close_upload(upload)
    return bytes(payload)


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 33:
        raise ApiError(400, ReasonCode.INVALID_IMAGE, "invalid PNG image")
    if payload[8:12] != (13).to_bytes(4, "big") or payload[12:16] != b"IHDR":
        raise ApiError(400, ReasonCode.INVALID_IMAGE, "invalid PNG header")
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    if width <= 0 or height <= 0:
        raise ApiError(400, ReasonCode.INVALID_IMAGE, "invalid PNG dimensions")
    return width, height


def _jpeg_dimensions(payload: bytes) -> tuple[int, int]:
    if len(payload) < 4 or payload[:2] != b"\xff\xd8":
        raise ApiError(400, ReasonCode.INVALID_IMAGE, "invalid JPEG image")
    position = 2
    length = len(payload)
    while position < length:
        while position < length and payload[position] == 0xFF:
            position += 1
        if position >= length:
            break
        marker = payload[position]
        position += 1
        if marker in (0x00, 0x01) or 0xD0 <= marker <= 0xD9:
            continue
        if position + 2 > length:
            break
        segment_length = int.from_bytes(payload[position : position + 2], "big")
        if segment_length < 2 or position + segment_length > length:
            break
        if marker in _JPEG_SOF_MARKERS:
            if segment_length < 7:
                break
            height = int.from_bytes(payload[position + 3 : position + 5], "big")
            width = int.from_bytes(payload[position + 5 : position + 7], "big")
            if width <= 0 or height <= 0:
                break
            return width, height
        position += segment_length
    raise ApiError(400, ReasonCode.INVALID_IMAGE, "JPEG dimensions are unavailable")


def preflight_image_header(payload: bytes, *, max_pixels: int) -> tuple[int, int]:
    """Return trusted header dimensions before invoking a full image decoder."""

    if payload.startswith(_PNG_SIGNATURE):
        width, height = _png_dimensions(payload)
    elif payload.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(payload)
    else:
        raise ApiError(
            400,
            ReasonCode.UNSUPPORTED_FORMAT,
            "only JPEG and PNG images are supported",
        )
    if width * height > max_pixels:
        raise ApiError(
            413,
            ReasonCode.IMAGE_TOO_LARGE,
            "image dimensions exceed the configured pixel limit",
        )
    return width, height


async def read_image_upload(
    upload: UploadFile,
    *,
    max_bytes: int,
    max_pixels: int,
    deadline_monotonic: float,
) -> np.ndarray:
    payload = await _read_limited(
        upload,
        max_bytes=max_bytes,
        deadline_monotonic=deadline_monotonic,
    )
    width, height = preflight_image_header(payload, max_pixels=max_pixels)
    _check_deadline(deadline_monotonic)
    image = cv2.imdecode(
        np.frombuffer(payload, dtype=np.uint8),
        cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION,
    )
    _check_deadline(deadline_monotonic)
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim != 3
        or image.shape[2] != 3
        or image.shape[:2] != (height, width)
    ):
        raise ApiError(400, ReasonCode.INVALID_IMAGE, "image decoding failed")
    return np.ascontiguousarray(image)


def _safe_video_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in _VIDEO_SUFFIXES else ".video"


@asynccontextmanager
async def spool_video_upload(
    upload: UploadFile,
    *,
    max_bytes: int,
    deadline_monotonic: float,
) -> AsyncIterator[SpooledVideo]:
    """Stream one video to a private local file and unconditionally remove it."""

    try:
        _check_deadline(deadline_monotonic)
    except ApiError:
        await _close_upload(upload)
        raise
    descriptor, raw_path = tempfile.mkstemp(
        prefix="face-video-",
        suffix=_safe_video_suffix(upload.filename),
    )
    path = Path(raw_path)
    spooled = SpooledVideo(path)
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            while True:
                _check_deadline(deadline_monotonic)
                remaining = max_bytes - written
                chunk = await upload.read(min(_VIDEO_CHUNK_BYTES, remaining + 1))
                _check_deadline(deadline_monotonic)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ApiError(
                        413,
                        ReasonCode.VIDEO_TOO_LARGE,
                        "video upload exceeds the configured byte limit",
                    )
                await asyncio.to_thread(handle.write, chunk)
                _check_deadline(deadline_monotonic)
            await asyncio.to_thread(_flush_and_sync, handle)
            _check_deadline(deadline_monotonic)
        if written == 0:
            raise ApiError(400, ReasonCode.INVALID_VIDEO, "video upload is empty")
        yield spooled
    finally:
        try:
            await _close_upload(upload)
        finally:
            try:
                if descriptor >= 0:
                    os.close(descriptor)
            finally:
                _cleanup_untransferred(spooled)
