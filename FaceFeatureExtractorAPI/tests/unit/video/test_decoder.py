from __future__ import annotations

import multiprocessing
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.schemas import ReasonCode
from face_algorithm.video.decoder import (
    DecodeRequest,
    OpenCvSubprocessDecoder,
    VideoDeadlineExceeded,
    VideoDecodeError,
    VideoInputError,
    VideoMetadata,
    VideoResourceLimits,
    _opencv_decode_worker,
)


def _slow_worker(connection, *_args) -> None:
    try:
        time.sleep(30.0)
    finally:
        connection.close()


def _two_frame_worker(connection, *_args) -> None:
    try:
        connection.send(
            (
                "metadata",
                VideoMetadata(
                    width=4,
                    height=3,
                    fps=2.0,
                    total_frames=2,
                    duration_seconds=1.0,
                    container="synthetic",
                ),
            )
        )
        connection.send(
            ("frame", 0, 0.0, np.full((3, 4, 3), 10, dtype=np.uint8))
        )
        connection.send(
            ("frame", 1, 0.5, np.full((3, 4, 3), 20, dtype=np.uint8))
        )
        connection.send(("done",))
    finally:
        connection.close()


def _boundary_fps_worker(connection, *_args) -> None:
    try:
        connection.send(
            (
                "metadata",
                VideoMetadata(
                    width=4,
                    height=3,
                    fps=120.0,
                    total_frames=1,
                    duration_seconds=1.0 / 120.0,
                    container="synthetic",
                ),
            )
        )
        connection.send(
            ("frame", 0, 0.0, np.full((3, 4, 3), 10, dtype=np.uint8))
        )
        connection.send(("done",))
    finally:
        connection.close()


def _excessive_fps_worker(connection, *_args) -> None:
    try:
        connection.send(
            (
                "metadata",
                VideoMetadata(
                    width=4,
                    height=3,
                    fps=120.01,
                    total_frames=1,
                    duration_seconds=1.0 / 120.01,
                    container="synthetic",
                ),
            )
        )
        connection.send(("done",))
    finally:
        connection.close()


def _limits(
    *,
    max_video_bytes: int = 1024,
    max_video_fps: float = 120.0,
) -> VideoResourceLimits:
    return VideoResourceLimits(
        max_video_bytes=max_video_bytes,
        max_duration_seconds=600.0,
        max_video_fps=max_video_fps,
        max_frame_pixels=25_000_000,
        max_analyzed_frames=1200,
    )


def _request() -> DecodeRequest:
    return DecodeRequest(
        mode="quick",
        quick_sample_count=12,
        include_first_last=True,
        full_interval_seconds=0.5,
    )


class _RecordingConnection:
    def __init__(self) -> None:
        self.messages: list[tuple[object, ...]] = []

    def send(self, message: tuple[object, ...]) -> None:
        self.messages.append(message)

    def close(self) -> None:
        pass


class _RecordingCapture:
    def __init__(self, *, total_frames: int = 10, fps: float = 4.0) -> None:
        self.total_frames = total_frames
        self.fps = fps
        self.position = 0
        self.seek_indices: list[int] = []
        self.read_indices: list[int] = []

    def isOpened(self) -> bool:
        return True

    def get(self, property_id: int) -> float:
        return {
            1: 4.0,
            2: 3.0,
            3: self.fps,
            4: float(self.total_frames),
        }[property_id]

    def set(self, property_id: int, value: float) -> bool:
        assert property_id == 5
        self.position = int(value)
        self.seek_indices.append(self.position)
        return True

    def read(self) -> tuple[bool, np.ndarray]:
        index = self.position
        self.position += 1
        self.read_indices.append(index)
        return True, np.full((3, 4, 3), index, dtype=np.uint8)

    def release(self) -> None:
        pass


def _install_fake_cv2(monkeypatch: pytest.MonkeyPatch, capture: _RecordingCapture) -> None:
    fake_cv2 = SimpleNamespace(
        CAP_PROP_FRAME_WIDTH=1,
        CAP_PROP_FRAME_HEIGHT=2,
        CAP_PROP_FPS=3,
        CAP_PROP_FRAME_COUNT=4,
        CAP_PROP_POS_FRAMES=5,
        VideoCapture=lambda _path: capture,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)


def test_video_size_is_rejected_before_a_worker_is_started(tmp_path: Path) -> None:
    video_path = tmp_path / "oversize.mp4"
    video_path.write_bytes(b"x" * 11)
    decoder = OpenCvSubprocessDecoder(worker_target=lambda *_args: None)

    with pytest.raises(VideoInputError) as caught:
        decoder.open(
            video_path,
            request=_request(),
            limits=_limits(max_video_bytes=10),
            deadline_monotonic=time.monotonic() + 2.0,
        )

    assert caught.value.reason_code is ReasonCode.VIDEO_TOO_LARGE


def test_subprocess_decoder_streams_bounded_frames_in_order(tmp_path: Path) -> None:
    video_path = tmp_path / "two-frames.mp4"
    video_path.write_bytes(b"not-customer-media")
    decoder = OpenCvSubprocessDecoder(worker_target=_two_frame_worker)

    with decoder.open(
        video_path,
        request=_request(),
        limits=_limits(),
        deadline_monotonic=time.monotonic() + 5.0,
    ) as stream:
        frames = tuple(stream)

    assert stream.metadata.total_frames == 2
    assert [frame.index for frame in frames] == [0, 1]
    assert [frame.timestamp_seconds for frame in frames] == [0.0, 0.5]
    assert [int(frame.image[0, 0, 0]) for frame in frames] == [10, 20]


def test_deadline_terminates_the_decode_worker(tmp_path: Path) -> None:
    video_path = tmp_path / "slow.mp4"
    video_path.write_bytes(b"not-customer-media")
    decoder = OpenCvSubprocessDecoder(worker_target=_slow_worker)
    started = time.monotonic()

    with pytest.raises(VideoDeadlineExceeded) as caught:
        decoder.open(
            video_path,
            request=_request(),
            limits=_limits(),
            deadline_monotonic=time.monotonic() + 0.25,
        )

    assert caught.value.reason_code is ReasonCode.ALGORITHM_TIMEOUT
    assert time.monotonic() - started < 3.0
    assert not multiprocessing.active_children()


def test_missing_video_is_a_structured_input_error(tmp_path: Path) -> None:
    decoder = OpenCvSubprocessDecoder()

    with pytest.raises(VideoInputError) as caught:
        decoder.open(
            tmp_path / "missing.mp4",
            request=_request(),
            limits=_limits(),
            deadline_monotonic=time.monotonic() + 1.0,
        )

    assert caught.value.reason_code is ReasonCode.VIDEO_DECODE_FAILED


def test_decode_contract_rejects_a_non_path_without_spawning() -> None:
    decoder = OpenCvSubprocessDecoder(worker_target=lambda *_args: None)

    with pytest.raises(VideoDecodeError) as caught:
        decoder.open(
            object(),
            request=_request(),
            limits=_limits(),
            deadline_monotonic=time.monotonic() + 1.0,
        )

    assert caught.value.reason_code is ReasonCode.VIDEO_DECODE_FAILED


def test_full_worker_decodes_forward_without_random_seeks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "full.mp4"
    video_path.write_bytes(b"synthetic")
    capture = _RecordingCapture()
    connection = _RecordingConnection()
    _install_fake_cv2(monkeypatch, capture)

    _opencv_decode_worker(
        connection,
        str(video_path),
        DecodeRequest(
            mode="full",
            quick_sample_count=12,
            include_first_last=True,
            full_interval_seconds=0.5,
        ),
        VideoResourceLimits(
            max_video_bytes=1024,
            max_duration_seconds=600.0,
            max_video_fps=120.0,
            max_frame_pixels=100,
            max_analyzed_frames=4,
        ),
        video_path.stat().st_size,
    )

    frame_messages = [message for message in connection.messages if message[0] == "frame"]
    assert capture.seek_indices == []
    assert capture.read_indices == list(range(7))
    assert [message[1] for message in frame_messages] == [0, 2, 4, 6]
    assert [message[2] for message in frame_messages] == [0.0, 0.5, 1.0, 1.5]


def test_quick_worker_keeps_deterministic_random_seeks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "quick.mp4"
    video_path.write_bytes(b"synthetic")
    capture = _RecordingCapture()
    connection = _RecordingConnection()
    _install_fake_cv2(monkeypatch, capture)

    _opencv_decode_worker(
        connection,
        str(video_path),
        DecodeRequest(
            mode="quick",
            quick_sample_count=4,
            include_first_last=True,
            full_interval_seconds=0.5,
        ),
        _limits(),
        video_path.stat().st_size,
    )

    frame_messages = [message for message in connection.messages if message[0] == "frame"]
    assert capture.seek_indices == [0, 3, 6, 9]
    assert capture.read_indices == [0, 3, 6, 9]
    assert [message[1] for message in frame_messages] == [0, 3, 6, 9]


def test_worker_accepts_frame_rate_exactly_at_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "boundary-fps.mp4"
    video_path.write_bytes(b"synthetic")
    capture = _RecordingCapture(fps=120.0)
    connection = _RecordingConnection()
    _install_fake_cv2(monkeypatch, capture)

    _opencv_decode_worker(
        connection,
        str(video_path),
        DecodeRequest(
            mode="full",
            quick_sample_count=12,
            include_first_last=True,
            full_interval_seconds=0.5,
        ),
        _limits(max_video_fps=120.0),
        video_path.stat().st_size,
    )

    assert connection.messages[-1] == ("done",)
    assert capture.read_indices == [0]


def test_worker_rejects_frame_rate_above_limit_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "excessive-fps.mp4"
    video_path.write_bytes(b"synthetic")
    capture = _RecordingCapture(fps=120.01)
    connection = _RecordingConnection()
    _install_fake_cv2(monkeypatch, capture)

    _opencv_decode_worker(
        connection,
        str(video_path),
        DecodeRequest(
            mode="full",
            quick_sample_count=12,
            include_first_last=True,
            full_interval_seconds=0.5,
        ),
        _limits(max_video_fps=120.0),
        video_path.stat().st_size,
    )

    assert connection.messages[0][:2] == ("error", ReasonCode.VIDEO_TOO_LARGE.value)
    assert capture.read_indices == []


def test_parent_accepts_worker_metadata_at_frame_rate_limit(tmp_path: Path) -> None:
    video_path = tmp_path / "boundary-parent.mp4"
    video_path.write_bytes(b"not-customer-media")
    decoder = OpenCvSubprocessDecoder(worker_target=_boundary_fps_worker)

    with decoder.open(
        video_path,
        request=_request(),
        limits=_limits(max_video_fps=120.0),
        deadline_monotonic=time.monotonic() + 5.0,
    ) as stream:
        frames = tuple(stream)

    assert stream.metadata.fps == 120.0
    assert [frame.index for frame in frames] == [0]


def test_parent_rejects_worker_metadata_above_frame_rate_limit(
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "excessive-parent.mp4"
    video_path.write_bytes(b"not-customer-media")
    decoder = OpenCvSubprocessDecoder(worker_target=_excessive_fps_worker)
    stream = None

    try:
        with pytest.raises(VideoInputError) as caught:
            stream = decoder.open(
                video_path,
                request=_request(),
                limits=_limits(max_video_fps=120.0),
                deadline_monotonic=time.monotonic() + 5.0,
            )
    finally:
        if stream is not None:
            stream.close()

    assert caught.value.reason_code is ReasonCode.VIDEO_TOO_LARGE
    assert not multiprocessing.active_children()
