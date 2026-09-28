"""Controlled forward-only presence scan, independent of verification sampling.

OpenCV does not expose a reliable distinction between clean EOF and every codec
failure. Completion here means a bounded forward scan reached EOF, with no frame
contract violation and no substantial shortfall against container metadata. It
does not certify the absence of concealed codec corruption. Container frame
counts are estimates; only a small, explicitly bounded tail shortfall is accepted.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import numpy as np

from face_algorithm.schemas import ReasonCode
from face_algorithm.video.decoder import (
    DecodeRequest,
    DecodedFrame,
    VideoDeadlineExceeded,
    VideoDecodeError,
    VideoInputError,
    VideoMetadata,
    VideoResourceLimits,
    _receive_message,
    _send_error,
    _stop_process,
)

__all__ = ["OpenCvPresenceDecoder", "PresenceDecodeSummary"]

# Accommodate observed +1/+6 container overcounts, but never most of a short clip.
MAX_EOF_SHORTFALL_FRAMES = 6
MAX_EOF_SHORTFALL_FRACTION = 0.10
_TIME_EPSILON = 1e-7


@dataclass(frozen=True, slots=True)
class PresenceDecodeSummary:
    decoded_frames: int = 0
    sampled_frames: int = 0
    scan_complete: bool = False
    duration_seconds: float = 0.0
    timestamp_source: Literal["pts", "estimated", "mixed", "unknown"] = "unknown"


def _failure(message: str) -> VideoDecodeError:
    return VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, message)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise VideoDeadlineExceeded(ReasonCode.ALGORITHM_TIMEOUT, "video decode deadline exceeded")


def _receive_presence_message(process: Any, connection: Any, deadline: float) -> tuple[Any, ...]:
    """Keep the existing IPC checks, also bounding a partially available recv.

    ``poll`` only establishes that some data is available. A worker stalled in
    a large/partial send could otherwise strand the parent's synchronous recv.
    Killing the sole pipe writer at the deadline unblocks that receive with EOF;
    the shared cleanup code then reaps the process and closes the handles.
    """
    _check_deadline(deadline)
    watchdog_fired = threading.Event()

    def interrupt_receive() -> None:
        watchdog_fired.set()
        try:
            if process.is_alive():
                process.kill()
        except (OSError, ValueError):
            # The receiving thread may already have reaped and closed it.
            pass

    watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), interrupt_receive)
    watchdog.daemon = True
    watchdog.start()
    try:
        try:
            message = _receive_message(process, connection, deadline)
        except VideoDecodeError as error:
            if watchdog_fired.is_set():
                raise VideoDeadlineExceeded(ReasonCode.ALGORITHM_TIMEOUT, "video decode deadline exceeded") from error
            _check_deadline(deadline)
            raise
        if watchdog_fired.is_set():
            raise VideoDeadlineExceeded(ReasonCode.ALGORITHM_TIMEOUT, "video decode deadline exceeded")
        _check_deadline(deadline)
        return message
    finally:
        watchdog.cancel()
        watchdog.join()


def _frame_budget(limits: VideoResourceLimits, fps: float) -> int:
    # Independent of reported frame count and PTS: an underreported or stalled
    # timestamp stream must not run forever within a purported short duration.
    value = limits.max_duration_seconds * fps
    if not math.isfinite(value):
        raise _failure("video resource frame budget is not finite")
    return max(1, int(math.floor(value + _TIME_EPSILON)))


def _validate_metadata(metadata: VideoMetadata, limits: VideoResourceLimits) -> None:
    if not isinstance(metadata, VideoMetadata):
        raise _failure("decoder worker did not return valid metadata")
    for value in (metadata.width, metadata.height, metadata.total_frames):
        if type(value) is not int or value <= 0:
            raise _failure("video dimensions and frame count must be positive integers")
    for value in (metadata.fps, metadata.duration_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise _failure("video timing metadata is invalid")
    if metadata.width * metadata.height > limits.max_frame_pixels or metadata.fps > limits.max_video_fps:
        raise VideoInputError(ReasonCode.VIDEO_TOO_LARGE, "video dimensions or frame rate exceed the resource limit")
    if metadata.duration_seconds > limits.max_duration_seconds + _TIME_EPSILON:
        raise VideoInputError(ReasonCode.VIDEO_TOO_LONG, "video exceeds the duration limit")
    if not math.isclose(metadata.duration_seconds, metadata.total_frames / metadata.fps, rel_tol=1e-9):
        raise _failure("video metadata timing is inconsistent")


def _validate_frame(frame: Any, metadata: VideoMetadata, limits: VideoResourceLimits) -> None:
    if (
        not isinstance(frame, np.ndarray)
        or frame.dtype != np.uint8
        or frame.ndim != 3
        or frame.shape != (metadata.height, metadata.width, 3)
        or frame.shape[0] * frame.shape[1] > limits.max_frame_pixels
    ):
        raise _failure("decoder returned an invalid frame or changed dimensions")


def _check_eof(decoded: int, reported: int) -> None:
    tolerance = min(MAX_EOF_SHORTFALL_FRAMES, int(reported * MAX_EOF_SHORTFALL_FRACTION))
    if decoded <= 0 or reported - decoded > tolerance:
        raise _failure("video ended before the expected frame range")


class _Timeline:
    """Normalize usable CAP_PROP_POS_MSEC; label all repaired intervals."""

    def __init__(self, fps: float) -> None:
        self.fps = fps
        self.origin: float | None = None
        self.last_raw = -1.0
        self.last = -1.0
        self.used_pts = False
        self.used_estimate = False

    def timestamp(self, index: int, raw: float) -> float:
        valid = math.isfinite(raw) and raw >= 0.0
        if index == 0:
            if valid:
                self.origin = raw
                self.last_raw = raw
            else:
                self.used_estimate = True
            self.last = 0.0
            return self.last
        if valid and self.origin is None:
            self.origin = raw - index / self.fps
        candidate = raw - self.origin if valid and self.origin is not None else -1.0
        if valid and raw > self.last_raw and candidate > self.last:
            self.used_pts = True
            self.last = candidate
        else:
            self.used_estimate = True
            self.last = max(index / self.fps, self.last + 1.0 / self.fps)
        if valid:
            self.last_raw = max(self.last_raw, raw)
        return self.last

    @property
    def source(self) -> Literal["pts", "estimated", "mixed"]:
        if self.used_pts:
            return "mixed" if self.used_estimate else "pts"
        # One frame, unavailable PTS, or a constant-zero PTS backend cannot
        # establish a precise timeline. Never advertise these as exact PTS.
        return "estimated"


def _opencv_presence_worker(
    connection: Any,
    video_path: str,
    request: DecodeRequest,
    limits: VideoResourceLimits,
    expected_size: int,
    deadline_monotonic: float,
) -> None:
    capture = None
    try:
        path = Path(video_path)
        if path.stat().st_size != expected_size or expected_size > limits.max_video_bytes:
            raise _failure("video changed after resource validation")
        import cv2

        capture = cv2.VideoCapture(video_path)
        _check_deadline(deadline_monotonic)
        if not capture.isOpened():
            raise _failure("video container could not be opened")
        values = [float(capture.get(prop)) for prop in (
            cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT,
            cv2.CAP_PROP_FPS, cv2.CAP_PROP_FRAME_COUNT,
        )]
        if not all(math.isfinite(v) for v in values):
            raise _failure("video metadata is not finite")
        width, height, fps, total = values
        if fps <= 0 or any(v <= 0 or not v.is_integer() for v in (width, height, total)):
            raise _failure("video metadata is invalid")
        metadata = VideoMetadata(int(width), int(height), fps, int(total), total / fps,
                                 path.suffix.casefold().lstrip(".") or "unknown")
        _validate_metadata(metadata, limits)
        connection.send(("metadata", metadata))
        timeline = _Timeline(fps)
        budget = _frame_budget(limits, fps)
        decoded = sampled = 0
        next_sample = 0.0
        last_sample_index = -1
        last_frame = None
        duration = 0.0

        def send_frame(index: int, timestamp: float, frame: np.ndarray) -> None:
            nonlocal sampled, last_sample_index
            if sampled >= limits.max_analyzed_frames:
                raise VideoInputError(ReasonCode.VIDEO_SCAN_LIMIT_REACHED, "video sample budget reached before scan completion")
            _check_deadline(deadline_monotonic)
            connection.send(("frame", index, timestamp, np.ascontiguousarray(frame)))
            sampled += 1
            last_sample_index = index

        while True:
            _check_deadline(deadline_monotonic)
            success, frame = capture.read()
            _check_deadline(deadline_monotonic)
            if not success:
                if frame is not None:
                    raise _failure("decoder returned a frame with an unsuccessful read")
                break
            _validate_frame(frame, metadata, limits)
            if decoded >= budget:
                raise VideoInputError(ReasonCode.VIDEO_TOO_LONG, "video exceeds the decoded-frame duration budget")
            timestamp = timeline.timestamp(decoded, float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0)
            duration = timestamp + 1.0 / fps
            if duration > limits.max_duration_seconds + _TIME_EPSILON:
                raise VideoInputError(ReasonCode.VIDEO_TOO_LONG, "decoded timeline exceeds the duration limit")
            if timestamp + _TIME_EPSILON >= next_sample:
                send_frame(decoded, timestamp, frame)
                next_sample = (math.floor((timestamp + _TIME_EPSILON) / request.full_interval_seconds) + 1) * request.full_interval_seconds
            last_frame = frame
            decoded += 1
        _check_eof(decoded, metadata.total_frames)
        if path.stat().st_size != expected_size:
            raise _failure("video changed during decoding")
        if last_sample_index != decoded - 1:
            send_frame(decoded - 1, timeline.last, last_frame)
        _check_deadline(deadline_monotonic)
        connection.send(("done", PresenceDecodeSummary(decoded, sampled, True, duration, timeline.source)))
    except VideoDecodeError as error:
        _send_error(connection, error.reason_code, str(error))
    except Exception:
        _send_error(connection, ReasonCode.VIDEO_DECODE_FAILED, "presence decoder failed internally")
    finally:
        try:
            if capture is not None:
                capture.release()
        finally:
            connection.close()


def _worker_error(message: tuple[Any, ...]) -> VideoDecodeError:
    if len(message) != 3 or not isinstance(message[2], str):
        return _failure("decoder worker returned an invalid error")
    try:
        reason = ReasonCode(message[1])
    except (ValueError, TypeError):
        return _failure("decoder worker returned an invalid reason")
    if reason == ReasonCode.ALGORITHM_TIMEOUT:
        return VideoDeadlineExceeded(reason, message[2])
    if reason in (ReasonCode.VIDEO_TOO_LARGE, ReasonCode.VIDEO_TOO_LONG, ReasonCode.VIDEO_SCAN_LIMIT_REACHED):
        return VideoInputError(reason, message[2])
    return VideoDecodeError(reason, message[2])


class _PresenceStream:
    def __init__(self, process: Any, connection: Any, metadata: VideoMetadata,
                 limits: VideoResourceLimits, deadline: float) -> None:
        self.metadata = metadata
        self.summary = PresenceDecodeSummary()
        self._process = process
        self._connection = connection
        self._limits = limits
        self._deadline = deadline
        self._closed = False
        self._done = False
        self._index = -1
        self._timestamp = -1.0
        self._received = 0

    def __enter__(self) -> _PresenceStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[DecodedFrame]:
        while not self._done and not self._closed:
            try:
                message = _receive_presence_message(self._process, self._connection, self._deadline)
                if message[0] == "error":
                    raise _worker_error(message)
                if message[0] == "done":
                    self._finish(message)
                    self.close()
                    return
                if message[0] != "frame" or len(message) != 4:
                    raise _failure("decoder worker returned an invalid frame message")
                index, timestamp, frame = message[1:]
                if (type(index) is not int or index <= self._index
                    or index >= _frame_budget(self._limits, self.metadata.fps)
                    or (self._received == 0 and index != 0)
                    or isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                    or not math.isfinite(timestamp) or timestamp < 0 or timestamp <= self._timestamp
                    or (self._received == 0 and timestamp != 0.0)):
                    raise _failure("decoder worker violated the frame timing contract")
                _validate_frame(frame, self.metadata, self._limits)
                if timestamp + 1 / self.metadata.fps > self._limits.max_duration_seconds + _TIME_EPSILON:
                    raise VideoInputError(ReasonCode.VIDEO_TOO_LONG, "decoded timeline exceeds the duration limit")
                if self._received >= self._limits.max_analyzed_frames:
                    raise VideoInputError(ReasonCode.VIDEO_SCAN_LIMIT_REACHED, "video sample budget exceeded")
                self._index = index
                self._timestamp = float(timestamp)
                self._received += 1
                self.summary = replace(self.summary, decoded_frames=index + 1,
                    sampled_frames=self._received, duration_seconds=timestamp + 1 / self.metadata.fps)
                owned = np.ascontiguousarray(frame).copy()
                owned.setflags(write=False)
            except BaseException:
                self.close()
                raise
            yield DecodedFrame(index, float(timestamp), owned)

    def _finish(self, message: tuple[Any, ...]) -> None:
        if len(message) != 2 or not isinstance(message[1], PresenceDecodeSummary):
            raise _failure("decoder worker returned an invalid completion summary")
        summary = message[1]
        if (summary.scan_complete is not True
            or type(summary.decoded_frames) is not int or summary.decoded_frames != self._index + 1
            or type(summary.sampled_frames) is not int or summary.sampled_frames != self._received
            or self._received <= 0 or summary.decoded_frames < summary.sampled_frames
            or isinstance(summary.duration_seconds, bool) or not isinstance(summary.duration_seconds, (int, float))
            or not math.isfinite(summary.duration_seconds)
            or not math.isclose(summary.duration_seconds, self._timestamp + 1 / self.metadata.fps, rel_tol=1e-9)
            or summary.timestamp_source not in ("pts", "estimated", "mixed")):
            raise _failure("decoder worker returned an inconsistent completion summary")
        _check_eof(summary.decoded_frames, self.metadata.total_frames)
        self.summary = summary
        self._done = True

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _stop_process(self._process, self._connection)


class OpenCvPresenceDecoder:
    """Read every frame in a killable spawn worker, returning bounded samples."""

    def __init__(self, *, worker_target: Callable[..., None] | None = None) -> None:
        self._worker_target = worker_target or _opencv_presence_worker

    def open(self, video_path: str | os.PathLike[str], *, request: DecodeRequest,
             limits: VideoResourceLimits, deadline_monotonic: float) -> _PresenceStream:
        if not isinstance(request, DecodeRequest) or not isinstance(limits, VideoResourceLimits):
            raise _failure("decoder request and limits must be validated contracts")
        if request.mode != "full" or request.include_first_last is not True:
            raise _failure("presence decoding requires full mode with first and last frames")
        if (isinstance(deadline_monotonic, bool) or not isinstance(deadline_monotonic, (int, float))
            or not math.isfinite(deadline_monotonic)):
            raise _failure("deadline must be a finite absolute monotonic timestamp")
        _check_deadline(deadline_monotonic)
        try:
            path = Path(video_path)
            stat = path.stat()
        except (OSError, TypeError, ValueError) as error:
            raise VideoInputError(ReasonCode.VIDEO_DECODE_FAILED, "video file is unavailable") from error
        if not path.is_file() or stat.st_size <= 0:
            raise VideoInputError(ReasonCode.VIDEO_DECODE_FAILED, "video path must identify a non-empty regular file")
        if stat.st_size > limits.max_video_bytes:
            raise VideoInputError(ReasonCode.VIDEO_TOO_LARGE, "video exceeds the byte limit")
        context = multiprocessing.get_context("spawn")
        receiving, sending = context.Pipe(duplex=False)
        process = context.Process(target=self._worker_target,
            args=(sending, str(path.resolve()), request, limits, stat.st_size, deadline_monotonic), daemon=True)
        try:
            process.start()
        except Exception as error:
            receiving.close()
            sending.close()
            process.close()
            raise _failure("decoder worker could not start") from error
        sending.close()
        try:
            message = _receive_presence_message(process, receiving, deadline_monotonic)
            if message[0] == "error":
                raise _worker_error(message)
            if message[0] != "metadata" or len(message) != 2:
                raise _failure("decoder worker did not return valid metadata")
            _validate_metadata(message[1], limits)
            return _PresenceStream(process, receiving, message[1], limits, deadline_monotonic)
        except BaseException:
            _stop_process(process, receiving)
            raise
