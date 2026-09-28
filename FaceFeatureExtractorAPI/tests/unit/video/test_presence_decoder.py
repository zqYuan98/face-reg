from __future__ import annotations

import importlib
import multiprocessing
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.schemas import ReasonCode
from face_algorithm.video.decoder import (
    DecodeRequest,
    VideoDeadlineExceeded,
    VideoDecodeError,
    VideoInputError,
    VideoMetadata,
    VideoResourceLimits,
)


def _api():
    spec = importlib.util.find_spec("face_algorithm.video.presence_decoder")
    assert spec is not None, "presence requires its own controlled sequential decoder"
    return importlib.import_module(spec.name)


def _request():
    return DecodeRequest("full", 12, True, 0.5)


def _limits(**changes):
    return replace(VideoResourceLimits(1024, 600.0, 120.0, 100, 1200), **changes)


class _Connection:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send(self, message):
        self.messages.append(message)

    def close(self):
        self.closed = True


class _Capture:
    def __init__(self, actual=10, reported=None, fps=4.0, pts=None, opened=True):
        self.actual = actual
        self.reported = actual if reported is None else reported
        self.fps = fps
        self.pts = pts
        self.opened = opened
        self.position = 0
        self.released = False
        self.frames = {}

    def isOpened(self):
        return self.opened

    def get(self, prop):
        if prop == 6:
            i = max(0, self.position - 1)
            return self.pts[i] * 1000 if self.pts is not None else i / self.fps * 1000
        return {1: 4.0, 2: 3.0, 3: self.fps, 4: self.reported}[prop]

    def set(self, *_args):
        pytest.fail("presence decoder must never seek")

    def read(self):
        if self.position >= self.actual:
            return False, None
        i = self.position
        self.position += 1
        return True, self.frames.get(i, np.full((3, 4, 3), i % 256, dtype=np.uint8))

    def release(self):
        self.released = True


def _run_worker(tmp_path, monkeypatch, capture, limits=None, deadline=None):
    api = _api()
    path = tmp_path / "synthetic.bin"
    path.write_bytes(b"synthetic")
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        CAP_PROP_FRAME_WIDTH=1, CAP_PROP_FRAME_HEIGHT=2, CAP_PROP_FPS=3,
        CAP_PROP_FRAME_COUNT=4, CAP_PROP_POS_FRAMES=5, CAP_PROP_POS_MSEC=6,
        VideoCapture=lambda _path: capture,
    ))
    connection = _Connection()
    api._opencv_presence_worker(
        connection, str(path), _request(), limits or _limits(), path.stat().st_size,
        time.monotonic() + 30 if deadline is None else deadline,
    )
    assert capture.released
    assert connection.closed
    return connection.messages


def test_presence_decoder_is_independent():
    assert _api().OpenCvPresenceDecoder


@pytest.mark.parametrize("extra", [0, 1, 6])
def test_reads_to_actual_eof_and_samples_real_last_frame(tmp_path, monkeypatch, extra):
    capture = _Capture(actual=100, reported=100 + extra, fps=25.0)
    messages = _run_worker(tmp_path, monkeypatch, capture)
    frames = [m for m in messages if m[0] == "frame"]
    assert [m[1] for m in frames] == [0, 13, 25, 38, 50, 63, 75, 88, 99]
    assert messages[-1][0] == "done"
    summary = messages[-1][1]
    assert summary.decoded_frames == 100
    assert summary.sampled_frames == 9
    assert summary.duration_seconds == pytest.approx(4.0)
    assert summary.scan_complete is True
    assert summary.timestamp_source == "pts"


@pytest.mark.parametrize("actual", [1, 3, 5])
def test_terminal_frame_is_not_duplicated(tmp_path, monkeypatch, actual):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=actual))
    indices = [m[1] for m in messages if m[0] == "frame"]
    assert indices[-1] == actual - 1
    assert len(indices) == len(set(indices))


@pytest.mark.parametrize("pts,source,expected", [
    ([4.0, 4.2, 4.7, 4.9], "pts", [0.0, 0.7, 0.9]),
    ([0.0] * 4, "estimated", [0.0, 0.5, 0.75]),
    ([float("nan")] * 4, "estimated", [0.0, 0.5, 0.75]),
    ([0.0, 0.3, 0.2, 0.8], "mixed", [0.0, 0.55, 0.8]),
])
def test_pts_are_normalized_or_explicitly_estimated(tmp_path, monkeypatch, pts, source, expected):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=4, pts=pts))
    assert messages[-1][0] == "done"
    assert messages[-1][1].timestamp_source == source
    assert [m[2] for m in messages if m[0] == "frame"] == pytest.approx(expected)


@pytest.mark.parametrize("capture", [
    _Capture(actual=0, reported=10), _Capture(opened=False),
    _Capture(reported=float("nan")), _Capture(fps=0.0),
    _Capture(actual=10, reported=100), _Capture(actual=1, reported=7),
    _Capture(actual=90, reported=100),
])
def test_empty_invalid_or_severely_truncated_stream_fails_closed(tmp_path, monkeypatch, capture):
    messages = _run_worker(tmp_path, monkeypatch, capture)
    assert messages[-1][:2] == ("error", ReasonCode.VIDEO_DECODE_FAILED.value)
    assert not any(m[0] == "done" for m in messages)


@pytest.mark.parametrize("frame", [
    np.zeros((4, 4, 3), dtype=np.uint8), np.zeros((3, 4), dtype=np.uint8),
    np.zeros((3, 4, 3), dtype=np.float32), np.zeros((0, 4, 3), dtype=np.uint8),
])
def test_every_decoded_frame_is_validated_even_if_not_sampled(tmp_path, monkeypatch, frame):
    capture = _Capture()
    capture.frames[1] = frame
    messages = _run_worker(tmp_path, monkeypatch, capture)
    assert messages[-1][:2] == ("error", ReasonCode.VIDEO_DECODE_FAILED.value)


def test_actual_pts_duration_over_limit_cannot_hide_behind_metadata(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=4, pts=[0, 0.5, 1, 3]),
                           _limits(max_duration_seconds=2.0))
    assert messages[-1][:2] == ("error", ReasonCode.VIDEO_TOO_LONG.value)


def test_total_decoded_frame_budget_uses_fps_even_if_pts_advance_slowly(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch,
                           _Capture(actual=30, reported=4, pts=[i * .001 for i in range(30)]),
                           _limits(max_duration_seconds=2.0))
    assert messages[-1][:2] == ("error", ReasonCode.VIDEO_TOO_LONG.value)


def test_sample_budget_fails_instead_of_silently_stopping(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(), _limits(max_analyzed_frames=2))
    assert messages[-1][:2] == ("error", "VIDEO_SCAN_LIMIT_REACHED")
    assert not any(m[0] == "done" for m in messages)


def test_last_frame_also_counts_toward_sample_budget(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=4), _limits(max_analyzed_frames=2))
    assert [m[1] for m in messages if m[0] == "frame"] == [0, 2]
    assert messages[-1][:2] == ("error", "VIDEO_SCAN_LIMIT_REACHED")


def test_underreported_count_never_limits_the_forward_scan(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=10, reported=3))
    assert messages[-1][0] == "done"
    assert messages[-1][1].decoded_frames == 10
    assert [m[1] for m in messages if m[0] == "frame"][-1] == 9


@pytest.mark.parametrize("limits,reason", [
    (_limits(max_frame_pixels=11), ReasonCode.VIDEO_TOO_LARGE),
    (_limits(max_video_fps=3), ReasonCode.VIDEO_TOO_LARGE),
    (_limits(max_duration_seconds=1), ReasonCode.VIDEO_TOO_LONG),
])
def test_unsafe_metadata_rejected_before_any_frame(tmp_path, monkeypatch, limits, reason):
    capture = _Capture()
    messages = _run_worker(tmp_path, monkeypatch, capture, limits)
    assert messages[-1][:2] == ("error", reason.value)
    assert capture.position == 0


def test_wall_clock_checked_during_sequential_decode(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(), deadline=time.monotonic() - 1)
    assert messages[-1][:2] == ("error", ReasonCode.ALGORITHM_TIMEOUT.value)


def _metadata():
    return VideoMetadata(4, 3, 2.0, 2, 1.0, "synthetic")


def _normal_worker(connection, *_args):
    api = importlib.import_module("face_algorithm.video.presence_decoder")
    connection.send(("metadata", _metadata()))
    for index in range(2):
        connection.send(("frame", index, index / 2, np.zeros((3, 4, 3), np.uint8)))
    connection.send(("done", api.PresenceDecodeSummary(2, 2, True, 1.0, "pts")))
    connection.close()


def _hanging_worker(connection, *_args):
    connection.send(("metadata", _metadata()))
    connection.send(("frame", 0, 0.0, np.zeros((3, 4, 3), np.uint8)))
    time.sleep(30)


def _invalid_done_worker(connection, *_args):
    connection.send(("metadata", _metadata()))
    connection.send(("done",))
    connection.close()


def _worker_with_unsafe_metadata(connection, *_args):
    connection.send(("metadata", replace(_metadata(), width=True)))
    connection.close()


def _worker_with_invalid_frame(connection, *_args):
    connection.send(("metadata", _metadata()))
    connection.send(("frame", True, 0.0, np.zeros((3, 4, 3), np.uint8)))
    connection.close()


def _worker_without_done(connection, *_args):
    connection.send(("metadata", _metadata()))
    connection.send(("frame", 0, 0.0, np.zeros((3, 4, 3), np.uint8)))
    connection.close()


def _worker_with_false_summary(connection, *_args):
    api = importlib.import_module("face_algorithm.video.presence_decoder")
    connection.send(("metadata", _metadata()))
    connection.send(("frame", 0, 0.0, np.zeros((3, 4, 3), np.uint8)))
    connection.send(("done", api.PresenceDecodeSummary(2, 2, True, 1.0, "pts")))
    connection.close()


def _worker_with_resource_failure(connection, *_args):
    connection.send(("metadata", _metadata()))
    connection.send(("error", "VIDEO_SCAN_LIMIT_REACHED", "sample budget"))
    connection.close()


def _open(tmp_path, worker, **kwargs):
    path = tmp_path / "worker-input.bin"
    path.write_bytes(b"synthetic")
    return _api().OpenCvPresenceDecoder(worker_target=worker).open(
        path, request=_request(), limits=_limits(),
        deadline_monotonic=kwargs.get("deadline", time.monotonic() + 10),
    )


def test_parent_publishes_summary_only_after_successful_eof(tmp_path):
    with _open(tmp_path, _normal_worker) as stream:
        assert not stream.summary.scan_complete
        frames = list(stream)
        assert stream.summary.scan_complete
        assert stream.summary.decoded_frames == 2
        assert all(not f.image.flags.writeable for f in frames)
    assert not multiprocessing.active_children()


def test_early_consumer_close_kills_worker_and_never_claims_complete(tmp_path):
    with _open(tmp_path, _hanging_worker) as stream:
        assert next(iter(stream)).index == 0
    assert not stream.summary.scan_complete
    assert not multiprocessing.active_children()


def test_consumer_exception_cleans_worker(tmp_path):
    with pytest.raises(RuntimeError, match="consumer"):
        with _open(tmp_path, _hanging_worker) as stream:
            next(iter(stream))
            raise RuntimeError("consumer")
    assert not stream.summary.scan_complete
    assert not multiprocessing.active_children()


def test_invalid_completion_is_rejected_and_worker_reaped(tmp_path):
    with _open(tmp_path, _invalid_done_worker) as stream:
        with pytest.raises(VideoDecodeError):
            list(stream)
        assert not stream.summary.scan_complete
    assert not multiprocessing.active_children()


@pytest.mark.parametrize("worker", [_worker_with_invalid_frame, _worker_without_done,
                                     _worker_with_false_summary])
def test_bad_ipc_never_completes_or_leaks_worker(tmp_path, worker):
    stream = _open(tmp_path, worker)
    with pytest.raises(VideoDecodeError):
        list(stream)
    assert not stream.summary.scan_complete
    assert not multiprocessing.active_children()


def test_unsafe_ipc_metadata_never_yields_stream(tmp_path):
    with pytest.raises(VideoDecodeError):
        _open(tmp_path, _worker_with_unsafe_metadata)
    assert not multiprocessing.active_children()


def test_sample_limit_ipc_preserves_resource_reason(tmp_path):
    stream = _open(tmp_path, _worker_with_resource_failure)
    with pytest.raises(VideoInputError) as caught:
        list(stream)
    assert caught.value.reason_code == ReasonCode.VIDEO_SCAN_LIMIT_REACHED
    assert not stream.summary.scan_complete
    assert not multiprocessing.active_children()


def test_parent_exposes_confirmed_progress_before_completion(tmp_path):
    with _open(tmp_path, _normal_worker) as stream:
        iterator = iter(stream)
        next(iterator)
        assert stream.summary.decoded_frames == 1
        assert stream.summary.sampled_frames == 1
        assert stream.summary.duration_seconds == .5
        assert not stream.summary.scan_complete
        next(iterator)
        assert stream.summary.decoded_frames == 2
        assert stream.summary.sampled_frames == 2
        assert not stream.summary.scan_complete
        with pytest.raises(StopIteration):
            next(iterator)
        assert stream.summary.scan_complete


@pytest.mark.parametrize("path", [None, 42, "missing-file.mp4"])
def test_missing_or_invalid_path_is_structured_failure(path):
    with pytest.raises(VideoInputError):
        _api().OpenCvPresenceDecoder().open(path, request=_request(), limits=_limits(),
            deadline_monotonic=time.monotonic() + 1)
    assert not multiprocessing.active_children()


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), "tomorrow"])
def test_invalid_deadline_fails_before_spawn(tmp_path, deadline):
    with pytest.raises(VideoDecodeError):
        _open(tmp_path, _normal_worker, deadline=deadline)
    assert not multiprocessing.active_children()


def test_presence_rejects_quick_mode_instead_of_silently_changing_it(tmp_path):
    with pytest.raises(VideoDecodeError):
        _api().OpenCvPresenceDecoder().open(tmp_path / "unused.mp4",
            request=replace(_request(), mode="quick"), limits=_limits(),
            deadline_monotonic=time.monotonic() + 1)


def test_hung_decode_is_terminated_at_absolute_deadline(tmp_path):
    with pytest.raises(VideoDeadlineExceeded):
        with _open(tmp_path, _hanging_worker, deadline=time.monotonic() + .7) as stream:
            list(stream)
    assert not multiprocessing.active_children()


def test_deadline_also_interrupts_receiving_a_partial_ipc_message():
    stopped = threading.Event()

    class PartialConnection:
        def poll(self, _timeout):
            return True

        def recv(self):
            stopped.wait(1.5)
            raise EOFError("partial IPC message")

        def close(self):
            pass

    class SendingProcess:
        def is_alive(self):
            return not stopped.is_set()

        def terminate(self):
            stopped.set()

        kill = terminate

        def join(self, timeout=None):
            pass

        def close(self):
            pass

    started = time.monotonic()
    stream = _api()._PresenceStream(SendingProcess(), PartialConnection(), _metadata(),
                                    _limits(), started + .05)
    with pytest.raises(VideoDeadlineExceeded):
        list(stream)
    assert time.monotonic() - started < .7
    assert stopped.is_set()
    assert not stream.summary.scan_complete


def test_input_byte_budget_rejected_before_spawning(tmp_path):
    path = tmp_path / "too-big.bin"
    path.write_bytes(b"x" * 20)
    with pytest.raises(VideoInputError) as caught:
        _api().OpenCvPresenceDecoder().open(path, request=_request(),
            limits=_limits(max_video_bytes=10), deadline_monotonic=time.monotonic() + 5)
    assert caught.value.reason_code == ReasonCode.VIDEO_TOO_LARGE


def test_generated_video_sequential_decode(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "generated.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (32, 24))
    assert writer.isOpened()
    try:
        for index in range(21):
            writer.write(np.full((24, 32, 3), index * 10, np.uint8))
    finally:
        writer.release()
    with _api().OpenCvPresenceDecoder().open(path, request=_request(),
        limits=_limits(max_video_bytes=1_000_000, max_frame_pixels=1000),
        deadline_monotonic=time.monotonic() + 15) as stream:
        frames = list(stream)
        assert [f.index for f in frames] == [0, 5, 10, 15, 20]
        assert stream.summary.scan_complete
        assert stream.summary.decoded_frames == 21
        assert stream.summary.duration_seconds == pytest.approx(2.1)
