"""Resource-bounded OpenCV decoding in a killable subprocess."""

from __future__ import annotations

import math
import multiprocessing
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Protocol, runtime_checkable

import numpy as np

from face_algorithm.schemas import ReasonCode
from face_algorithm.video.sampler import full_sample_indices, quick_sample_indices

__all__ = [
    "DecodeRequest",
    "DecodedFrame",
    "OpenCvSubprocessDecoder",
    "VideoDeadlineExceeded",
    "VideoDecodeError",
    "VideoDecoder",
    "VideoInputError",
    "VideoMetadata",
    "VideoResourceLimits",
    "VideoStream",
]


class VideoDecodeError(RuntimeError):
    def __init__(self, reason_code: ReasonCode, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class VideoInputError(VideoDecodeError):
    """A video violates a validated media or resource contract."""


class VideoDeadlineExceeded(VideoDecodeError):
    """The controlled decoder exceeded its absolute wall-clock deadline."""


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    total_frames: int
    duration_seconds: float
    container: str


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    index: int
    timestamp_seconds: float
    image: np.ndarray


@dataclass(frozen=True, slots=True)
class DecodeRequest:
    mode: Literal["quick", "full"]
    quick_sample_count: int
    include_first_last: bool
    full_interval_seconds: float

    def __post_init__(self) -> None:
        if self.mode not in ("quick", "full"):
            raise ValueError("decode mode must be quick or full")
        if isinstance(self.quick_sample_count, bool) or not isinstance(
            self.quick_sample_count, int
        ):
            raise TypeError("quick_sample_count must be a positive integer")
        if self.quick_sample_count <= 0:
            raise ValueError("quick_sample_count must be a positive integer")
        if type(self.include_first_last) is not bool:
            raise TypeError("include_first_last must be a boolean")
        if (
            isinstance(self.full_interval_seconds, bool)
            or not isinstance(self.full_interval_seconds, (int, float))
            or not math.isfinite(float(self.full_interval_seconds))
            or self.full_interval_seconds <= 0
        ):
            raise ValueError("full_interval_seconds must be finite and positive")


@dataclass(frozen=True, slots=True)
class VideoResourceLimits:
    max_video_bytes: int
    max_duration_seconds: float
    max_video_fps: float
    max_frame_pixels: int
    max_analyzed_frames: int

    def __post_init__(self) -> None:
        for label in ("max_video_bytes", "max_frame_pixels", "max_analyzed_frames"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{label} must be a positive integer")
            if value <= 0:
                raise ValueError(f"{label} must be a positive integer")
        if (
            isinstance(self.max_duration_seconds, bool)
            or not isinstance(self.max_duration_seconds, (int, float))
            or not math.isfinite(float(self.max_duration_seconds))
            or self.max_duration_seconds <= 0
        ):
            raise ValueError("max_duration_seconds must be finite and positive")
        if (
            isinstance(self.max_video_fps, bool)
            or not isinstance(self.max_video_fps, (int, float))
            or not math.isfinite(float(self.max_video_fps))
            or self.max_video_fps <= 0
        ):
            raise ValueError("max_video_fps must be finite and positive")


@runtime_checkable
class VideoStream(Protocol):
    metadata: VideoMetadata

    def __iter__(self) -> Iterator[DecodedFrame]: ...
    def __enter__(self) -> "VideoStream": ...
    def __exit__(self, *args: object) -> None: ...


@runtime_checkable
class VideoDecoder(Protocol):
    def open(
        self,
        video_path: str | os.PathLike[str],
        *,
        request: DecodeRequest,
        limits: VideoResourceLimits,
        deadline_monotonic: float,
    ) -> VideoStream: ...


_WorkerTarget = Callable[..., None]


def _send_error(connection: Any, reason: ReasonCode, message: str) -> None:
    try:
        connection.send(("error", reason.value, message[:256]))
    except (BrokenPipeError, EOFError, OSError):
        pass


def _opencv_decode_worker(
    connection: Any,
    video_path: str,
    request: DecodeRequest,
    limits: VideoResourceLimits,
    expected_size: int,
) -> None:
    capture = None
    try:
        try:
            if Path(video_path).stat().st_size != expected_size:
                raise VideoInputError(
                    ReasonCode.VIDEO_DECODE_FAILED,
                    "video changed after resource validation",
                )
        except OSError as error:
            raise VideoInputError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video is unavailable",
            ) from error

        import cv2

        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video container could not be opened",
            )
        raw_width = float(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        raw_height = float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        raw_total = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not all(math.isfinite(value) for value in (raw_width, raw_height, fps, raw_total)):
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video metadata is not finite",
            )
        width, height, total_frames = int(raw_width), int(raw_height), int(raw_total)
        if width <= 0 or height <= 0 or total_frames <= 0 or fps <= 0.0:
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video metadata is invalid",
            )
        if fps > limits.max_video_fps:
            raise VideoInputError(
                ReasonCode.VIDEO_TOO_LARGE,
                "video frame rate exceeds the resource limit",
            )
        if width * height > limits.max_frame_pixels:
            raise VideoInputError(
                ReasonCode.VIDEO_TOO_LARGE,
                "decoded video frame exceeds the pixel limit",
            )
        duration = total_frames / fps
        if not math.isfinite(duration) or duration > limits.max_duration_seconds:
            raise VideoInputError(
                ReasonCode.VIDEO_TOO_LONG,
                "video exceeds the duration limit",
            )
        metadata = VideoMetadata(
            width=width,
            height=height,
            fps=fps,
            total_frames=total_frames,
            duration_seconds=duration,
            container=Path(video_path).suffix.casefold().lstrip(".") or "unknown",
        )
        connection.send(("metadata", metadata))

        if request.mode == "quick":
            indices = quick_sample_indices(
                total_frames=total_frames,
                sample_count=request.quick_sample_count,
                include_first_last=request.include_first_last,
            )
        else:
            indices = full_sample_indices(
                total_frames=total_frames,
                fps=fps,
                interval_seconds=request.full_interval_seconds,
                max_frames=limits.max_analyzed_frames,
            )
        decoded = 0
        next_sequential_index = 0
        for frame_index in indices:
            if request.mode == "quick":
                if not capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index)):
                    raise VideoDecodeError(
                        ReasonCode.VIDEO_DECODE_FAILED,
                        "video seek failed",
                    )
                read_indices = (frame_index,)
            else:
                read_indices = range(next_sequential_index, frame_index + 1)

            frame = None
            for decoded_index in read_indices:
                success, candidate = capture.read()
                if not success or not isinstance(candidate, np.ndarray):
                    raise VideoDecodeError(
                        ReasonCode.VIDEO_DECODE_FAILED,
                        "sampled video frame could not be decoded",
                    )
                if (
                    candidate.dtype != np.uint8
                    or candidate.ndim != 3
                    or candidate.shape[2] != 3
                    or candidate.shape[0] <= 0
                    or candidate.shape[1] <= 0
                    or candidate.shape[0] * candidate.shape[1] > limits.max_frame_pixels
                ):
                    raise VideoDecodeError(
                        ReasonCode.VIDEO_DECODE_FAILED,
                        "decoder returned an invalid frame",
                    )
                if candidate.shape[:2] != (height, width):
                    raise VideoDecodeError(
                        ReasonCode.VIDEO_DECODE_FAILED,
                        "decoded frame dimensions differ from metadata",
                    )
                if decoded_index == frame_index:
                    frame = candidate
            next_sequential_index = frame_index + 1
            if frame is None:
                raise VideoDecodeError(
                    ReasonCode.VIDEO_DECODE_FAILED,
                    "sampled video frame could not be decoded",
                )
            connection.send(
                ("frame", frame_index, frame_index / fps, np.ascontiguousarray(frame))
            )
            decoded += 1
        if decoded == 0:
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video yielded no sampled frames",
            )
        connection.send(("done",))
    except VideoDecodeError as error:
        _send_error(connection, error.reason_code, str(error))
    except Exception:
        _send_error(
            connection,
            ReasonCode.VIDEO_DECODE_FAILED,
            "video decoder failed internally",
        )
    finally:
        if capture is not None:
            capture.release()
        connection.close()


def _remaining(deadline_monotonic: float) -> float:
    return deadline_monotonic - time.monotonic()


def _stop_process(process: multiprocessing.Process, connection: Any) -> None:
    try:
        connection.close()
    except OSError:
        pass
    try:
        alive = process.is_alive()
    except ValueError:
        return
    if alive:
        process.terminate()
        process.join(timeout=1.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)
    else:
        process.join(timeout=0.1)
    try:
        process.close()
    except ValueError:
        pass


def _receive_message(
    process: multiprocessing.Process,
    connection: Any,
    deadline_monotonic: float,
) -> tuple[Any, ...]:
    while True:
        remaining = _remaining(deadline_monotonic)
        if remaining <= 0.0:
            _stop_process(process, connection)
            raise VideoDeadlineExceeded(
                ReasonCode.ALGORITHM_TIMEOUT,
                "video decode deadline exceeded",
            )
        try:
            if connection.poll(min(0.05, remaining)):
                message = connection.recv()
                if not isinstance(message, tuple) or not message:
                    raise VideoDecodeError(
                        ReasonCode.VIDEO_DECODE_FAILED,
                        "decoder worker returned an invalid message",
                    )
                return message
        except (EOFError, OSError) as error:
            _stop_process(process, connection)
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "decoder worker closed unexpectedly",
            ) from error
        if not process.is_alive():
            _stop_process(process, connection)
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "decoder worker exited unexpectedly",
            )


def _error_from_message(message: tuple[Any, ...]) -> VideoDecodeError:
    try:
        reason = ReasonCode(message[1])
        detail = str(message[2])
    except Exception as error:
        raise VideoDecodeError(
            ReasonCode.VIDEO_DECODE_FAILED,
            "decoder worker returned an invalid error",
        ) from error
    error_type = (
        VideoInputError
        if reason in (ReasonCode.VIDEO_TOO_LARGE, ReasonCode.VIDEO_TOO_LONG)
        else VideoDecodeError
    )
    return error_type(reason, detail)


class _ProcessVideoStream:
    def __init__(
        self,
        *,
        process: multiprocessing.Process,
        connection: Any,
        metadata: VideoMetadata,
        deadline_monotonic: float,
        max_frames: int,
        max_frame_pixels: int,
    ) -> None:
        self.metadata = metadata
        self._process = process
        self._connection = connection
        self._deadline_monotonic = deadline_monotonic
        self._max_frames = max_frames
        self._max_frame_pixels = max_frame_pixels
        self._closed = False
        self._done = False

    def __enter__(self) -> "_ProcessVideoStream":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[DecodedFrame]:
        previous_index = -1
        previous_timestamp = -1.0
        received = 0
        while not self._done:
            message = _receive_message(
                self._process,
                self._connection,
                self._deadline_monotonic,
            )
            kind = message[0]
            if kind == "done":
                self._done = True
                break
            if kind == "error":
                self.close()
                raise _error_from_message(message)
            if kind != "frame" or len(message) != 4:
                self.close()
                raise VideoDecodeError(
                    ReasonCode.VIDEO_DECODE_FAILED,
                    "decoder worker returned an invalid frame message",
                )
            index, timestamp, image = message[1:]
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or index <= previous_index
                or isinstance(timestamp, bool)
                or not isinstance(timestamp, (int, float))
                or not math.isfinite(float(timestamp))
                or float(timestamp) < previous_timestamp
                or not isinstance(image, np.ndarray)
                or image.dtype != np.uint8
                or image.ndim != 3
                or image.shape[2] != 3
                or image.shape[:2] != (self.metadata.height, self.metadata.width)
                or image.shape[0] * image.shape[1] > self._max_frame_pixels
                or received >= self._max_frames
            ):
                self.close()
                raise VideoDecodeError(
                    ReasonCode.VIDEO_DECODE_FAILED,
                    "decoder worker violated the frame contract",
                )
            previous_index = index
            previous_timestamp = float(timestamp)
            received += 1
            owned = np.ascontiguousarray(image).copy()
            owned.setflags(write=False)
            yield DecodedFrame(
                index=index,
                timestamp_seconds=float(timestamp),
                image=owned,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _stop_process(self._process, self._connection)


class OpenCvSubprocessDecoder:
    """Decode selected frames outside the inference process."""

    def __init__(self, *, worker_target: _WorkerTarget | None = None) -> None:
        self._worker_target = worker_target or _opencv_decode_worker

    def open(
        self,
        video_path: str | os.PathLike[str],
        *,
        request: DecodeRequest,
        limits: VideoResourceLimits,
        deadline_monotonic: float,
    ) -> VideoStream:
        if not isinstance(request, DecodeRequest) or not isinstance(
            limits, VideoResourceLimits
        ):
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "decoder request and limits must be validated contracts",
            )
        try:
            path = Path(video_path)
        except TypeError as error:
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video path must be path-like",
            ) from error
        try:
            stat = path.stat()
        except OSError as error:
            raise VideoInputError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video file is unavailable",
            ) from error
        if not path.is_file() or stat.st_size <= 0:
            raise VideoInputError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "video path must identify a non-empty regular file",
            )
        if stat.st_size > limits.max_video_bytes:
            raise VideoInputError(
                ReasonCode.VIDEO_TOO_LARGE,
                "video exceeds the byte limit",
            )
        if (
            isinstance(deadline_monotonic, bool)
            or not isinstance(deadline_monotonic, (int, float))
            or not math.isfinite(float(deadline_monotonic))
        ):
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "deadline must be a finite absolute monotonic timestamp",
            )

        context = multiprocessing.get_context("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=self._worker_target,
            args=(send_connection, str(path.resolve()), request, limits, stat.st_size),
            daemon=True,
        )
        try:
            process.start()
        except Exception as error:
            receive_connection.close()
            send_connection.close()
            raise VideoDecodeError(
                ReasonCode.VIDEO_DECODE_FAILED,
                "decoder worker could not start",
            ) from error
        send_connection.close()
        try:
            message = _receive_message(
                process,
                receive_connection,
                float(deadline_monotonic),
            )
            if message[0] == "error":
                raise _error_from_message(message)
            if (
                message[0] != "metadata"
                or len(message) != 2
                or not isinstance(message[1], VideoMetadata)
            ):
                raise VideoDecodeError(
                    ReasonCode.VIDEO_DECODE_FAILED,
                    "decoder worker did not return valid metadata",
                )
            metadata = message[1]
            if (
                metadata.width <= 0
                or metadata.height <= 0
                or metadata.width * metadata.height > limits.max_frame_pixels
                or metadata.total_frames <= 0
                or not math.isfinite(metadata.fps)
                or metadata.fps <= 0.0
                or not math.isfinite(metadata.duration_seconds)
                or metadata.duration_seconds <= 0.0
                or metadata.duration_seconds > limits.max_duration_seconds
            ):
                raise VideoDecodeError(
                    ReasonCode.VIDEO_DECODE_FAILED,
                    "decoder worker returned unsafe metadata",
                )
            if metadata.fps > limits.max_video_fps:
                raise VideoInputError(
                    ReasonCode.VIDEO_TOO_LARGE,
                    "video frame rate exceeds the resource limit",
                )
            max_frames = (
                request.quick_sample_count
                if request.mode == "quick"
                else limits.max_analyzed_frames
            )
            return _ProcessVideoStream(
                process=process,
                connection=receive_connection,
                metadata=metadata,
                deadline_monotonic=float(deadline_monotonic),
                max_frames=max_frames,
                max_frame_pixels=limits.max_frame_pixels,
            )
        except Exception:
            _stop_process(process, receive_connection)
            raise
