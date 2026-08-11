from __future__ import annotations

import asyncio
import tempfile
import threading
import time
from pathlib import Path

import pytest

from api.errors import ApiError
from api.media import read_image_upload, spool_video_upload


class _Upload:
    filename = "capture.mp4"

    def __init__(self) -> None:
        self.read_calls = 0
        self.closed = False

    async def read(self, size: int) -> bytes:
        self.read_calls += 1
        return b"not-reached"

    async def close(self) -> None:
        self.closed = True


def test_expired_image_deadline_stops_before_reading_upload() -> None:
    upload = _Upload()

    async def scenario() -> None:
        with pytest.raises(ApiError) as captured:
            await read_image_upload(
                upload,
                max_bytes=1024,
                max_pixels=1024,
                deadline_monotonic=time.monotonic() - 1.0,
            )
        assert captured.value.status_code == 504
        assert captured.value.reason_code.value == "ALGORITHM_TIMEOUT"

    asyncio.run(scenario())
    assert upload.read_calls == 0
    assert upload.closed is True


def test_expired_video_deadline_stops_before_creating_payload() -> None:
    upload = _Upload()

    async def scenario() -> None:
        with pytest.raises(ApiError) as captured:
            async with spool_video_upload(
                upload,
                max_bytes=1024,
                deadline_monotonic=time.monotonic() - 1.0,
            ):
                raise AssertionError("expired upload must not yield a path")
        assert captured.value.status_code == 504
        assert captured.value.reason_code.value == "ALGORITHM_TIMEOUT"

    asyncio.run(scenario())
    assert upload.read_calls == 0
    assert upload.closed is True


class _VideoUpload:
    filename = "capture.mp4"

    def __init__(self, payload: bytes, *, fail_close: bool = False) -> None:
        self._payload = payload
        self._sent = False
        self.fail_close = fail_close

    async def read(self, size: int) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._payload

    async def close(self) -> None:
        if self.fail_close:
            raise OSError("synthetic close failure")


def test_upload_close_failure_cannot_skip_private_temp_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _VideoUpload(b"video", fail_close=True)
    created: list[Path] = []
    original_mkstemp = tempfile.mkstemp

    def private_mkstemp(*args, **kwargs):
        descriptor, raw_path = original_mkstemp(*args, dir=tmp_path, **kwargs)
        created.append(Path(raw_path))
        return descriptor, raw_path

    monkeypatch.setattr("api.media.tempfile.mkstemp", private_mkstemp)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="request failed"):
            async with spool_video_upload(
                upload,
                max_bytes=1024,
                deadline_monotonic=time.monotonic() + 1.0,
            ):
                raise RuntimeError("request failed")

    asyncio.run(scenario())
    assert created
    assert all(not path.exists() for path in created)


def test_video_flush_and_fsync_do_not_block_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _VideoUpload(b"video")
    event_loop_thread = threading.current_thread().name
    fsync_threads: list[str] = []
    original_fsync = __import__("os").fsync

    def observed_fsync(descriptor: int) -> None:
        fsync_threads.append(threading.current_thread().name)
        original_fsync(descriptor)

    monkeypatch.setattr("api.media.os.fsync", observed_fsync)

    async def scenario() -> None:
        async with spool_video_upload(
            upload,
            max_bytes=1024,
            deadline_monotonic=time.monotonic() + 1.0,
        ) as spooled:
            assert spooled.path.is_file()

    asyncio.run(scenario())
    assert fsync_threads
    assert all(name != event_loop_thread for name in fsync_threads)
