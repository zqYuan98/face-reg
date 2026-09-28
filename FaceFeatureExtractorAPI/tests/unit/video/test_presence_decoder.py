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


def _run_worker(tmp_path, monkeypatch, capture, limits=None, deadline=None, request=None):
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
        connection, str(path), request or _request(), limits or _limits(), path.stat().st_size,
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
        path, request=kwargs.get("request", _request()), limits=kwargs.get("limits", _limits()),
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


def _presence_request(interval=.5, supplemental=2):
    return importlib.import_module("face_algorithm.presence.sampling").PresenceDecodeRequest(interval, supplemental)


def test_new_request_emits_base_and_two_later_candidates_per_window(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=18, fps=12), request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert [(m[1], m[4], m[5]) for m in frames] == [
        (0, 0, "base"), (2, 0, "supplemental"), (4, 0, "supplemental"),
        (6, 1, "base"), (8, 1, "supplemental"), (10, 1, "supplemental"),
        (12, 2, "base"), (14, 2, "supplemental"), (16, 2, "supplemental"), (17, 3, "base"),
    ]
    assert messages[-1][1].sampled_frames == 10
    assert messages[-1][1].scan_complete is True


def test_terminal_supplemental_is_upgraded_to_base_without_duplicate(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=17, fps=12), request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert frames[-1][1] == 16
    assert frames[-1][2] == pytest.approx(16 / 12)
    assert frames[-1][4:] == (3, "base")
    assert len(frames) == len({m[1] for m in frames}) == 9


def test_low_fps_target_collisions_preserve_base_priority(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=4, fps=1), request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    assert [(m[1], m[4], m[5]) for m in messages if m[0] == "frame"] == [
        (0, 0, "base"), (1, 2, "base"), (2, 4, "base"), (3, 6, "base"),
    ]


@pytest.mark.parametrize("budget,indices,complete", [(1, [0], False), (2, [0, 9], True), (3, [0, 5, 9], True)])
def test_new_request_small_budgets_reserve_first_and_last(tmp_path, monkeypatch, budget, indices, complete):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(), _limits(max_analyzed_frames=budget),
                           request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert [m[1] for m in frames] == indices
    assert messages[-1][1].decoded_frames == 10
    assert messages[-1][1].sampled_frames <= budget
    assert messages[-1][1].scan_complete is complete
    assert frames[0][5] == frames[-1][5] == "base"


def test_one_real_frame_can_complete_with_budget_one(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=1), _limits(max_analyzed_frames=1),
                           request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    assert messages[-1][1].scan_complete is True
    assert [(m[1], m[5]) for m in messages if m[0] == "frame"] == [(0, "base")]


def test_600_second_video_uniformly_uses_bounded_candidates_through_real_end(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=18000, fps=30), request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert frames[0][1] == 0
    assert frames[-1][1] == 17999
    assert frames[-1][5] == "base"
    assert len(frames) <= 1200
    assert any(m[2] > 590 and m[5] == "base" for m in frames)
    assert len(frames) == len({m[1] for m in frames})
    assert messages[-1][1].scan_complete is True


@pytest.mark.parametrize("capture", [
    _Capture(actual=10, reported=3),
    _Capture(actual=4, pts=[0, .2, .6, 1.2]),
])
def test_actual_timeline_beyond_planned_metadata_is_incomplete(tmp_path, monkeypatch, capture):
    messages = _run_worker(tmp_path, monkeypatch, capture, request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert frames[-1][1] == capture.actual - 1
    assert frames[-1][5] == "base"
    assert messages[-1][1].decoded_frames == capture.actual
    assert messages[-1][1].scan_complete is False


@pytest.mark.parametrize("extra", [1, 6])
def test_new_request_tolerates_only_bounded_container_tail_overcount(tmp_path, monkeypatch, extra):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=100, reported=100 + extra, fps=25),
                           request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    assert messages[-1][1].scan_complete is True
    assert [m for m in messages if m[0] == "frame"][-1][1] == 99


@pytest.mark.parametrize("capture,limits,reason", [
    (_Capture(actual=10, reported=100), _limits(), ReasonCode.VIDEO_DECODE_FAILED),
    (_Capture(reported=0), _limits(), ReasonCode.VIDEO_DECODE_FAILED),
    (_Capture(actual=4, pts=[0, .5, 1, 3]), _limits(max_duration_seconds=2), ReasonCode.VIDEO_TOO_LONG),
    (_Capture(actual=30, reported=4, pts=[i * .001 for i in range(30)]), _limits(max_duration_seconds=2), ReasonCode.VIDEO_TOO_LONG),
])
def test_new_request_preserves_decode_metadata_and_duration_errors(tmp_path, monkeypatch, capture, limits, reason):
    messages = _run_worker(tmp_path, monkeypatch, capture, limits, request=_presence_request())
    assert messages[-1][:2] == ("error", reason.value)


def _new_request_worker(connection, _path, request, *_args):
    api = importlib.import_module("face_algorithm.video.presence_decoder")
    connection.send(("metadata", _metadata()))
    connection.send(("frame", 0, 0.0, np.zeros((3, 4, 3), np.uint8), 0, "base"))
    connection.send(("frame", 1, .5, np.zeros((3, 4, 3), np.uint8), 1, "base"))
    connection.send(("done", api.PresenceDecodeSummary(2, 2, True, 1.0, "pts")))
    connection.close()


def test_parent_exposes_effective_interval_before_consuming_typed_frames(tmp_path):
    with _open(tmp_path, _new_request_worker, request=_presence_request(), limits=_limits(max_analyzed_frames=2)) as stream:
        assert stream.sampling_interval_seconds == 1.0
        frames = list(stream)
        sample_type = importlib.import_module("face_algorithm.presence.sampling").PresenceSampleFrame
        assert all(isinstance(f, sample_type) for f in frames)
        assert all(not f.image.flags.writeable for f in frames)
        assert frames[-1].kind == "base"
        assert stream.summary.sampled_frames == 2
        assert stream.summary.scan_complete is True
    assert not multiprocessing.active_children()


def _incomplete_new_request_worker(connection, *_args):
    api = importlib.import_module("face_algorithm.video.presence_decoder")
    connection.send(("metadata", _metadata()))
    connection.send(("frame", 0, 0.0, np.zeros((3, 4, 3), np.uint8), 0, "base"))
    connection.send(("done", api.PresenceDecodeSummary(2, 1, False, 1.0, "pts")))
    connection.close()


def test_parent_accepts_honest_incomplete_summary_with_budget_one(tmp_path):
    with _open(tmp_path, _incomplete_new_request_worker, request=_presence_request(), limits=_limits(max_analyzed_frames=1)) as stream:
        assert len(list(stream)) == 1
        assert stream.summary.decoded_frames == 2
        assert stream.summary.scan_complete is False
    assert not multiprocessing.active_children()


def test_generated_video_new_request_end_to_end(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "generated-presence.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 12.0, (32, 24))
    assert writer.isOpened()
    try:
        for index in range(17):
            writer.write(np.full((24, 32, 3), index * 10, np.uint8))
    finally:
        writer.release()
    with _api().OpenCvPresenceDecoder().open(path, request=_presence_request(),
        limits=_limits(max_video_bytes=1_000_000, max_frame_pixels=1000),
        deadline_monotonic=time.monotonic() + 15) as stream:
        assert stream.sampling_interval_seconds == .5
        frames = list(stream)
        assert [f.index for f in frames] == list(range(0, 17, 2))
        assert frames[-1].kind == "base"
        assert stream.summary.scan_complete
        assert stream.summary.sampled_frames == 9


def test_incomplete_summary_must_still_include_last_frame_when_budget_allows(tmp_path):
    with _open(tmp_path, _incomplete_new_request_worker, request=_presence_request(), limits=_limits(max_analyzed_frames=2)) as stream:
        with pytest.raises(VideoDecodeError):
            list(stream)
        assert not stream.summary.scan_complete
    assert not multiprocessing.active_children()


def test_supplemental_candidate_is_never_earlier_than_its_target(tmp_path, monkeypatch):
    target = 1 / 6
    capture = _Capture(actual=5, fps=10, pts=[0, target - 5e-8, target + .01, .34, .4])
    messages = _run_worker(tmp_path, monkeypatch, capture, request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert [m[1] for m in frames] == [0, 2, 3, 4]
    assert frames[1][2] >= target


def test_nearly_next_window_frame_cannot_be_selected_as_early_base(tmp_path, monkeypatch):
    capture = _Capture(actual=6, fps=6, pts=[0, .2, .4, .5 - 5e-8, .6, .8])
    messages = _run_worker(tmp_path, monkeypatch, capture, request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert [m[1] for m in frames if m[5] == "base"] == [0, 4, 5]


def _bad_sampling_worker(connection, _path, request, *_args):
    connection.send(("metadata", _metadata()))
    connection.send(("frame", 0, 0.0, np.zeros((3, 4, 3), np.uint8), 0, "base"))
    variant = request.sample_interval_seconds
    bad = {1.0: (0, "base"), 2.0: (1, "supplemental"), 3.0: (True, "base"), 4.0: (1, "other")}[variant]
    connection.send(("frame", 1, .5, np.zeros((3, 4, 3), np.uint8), *bad))
    connection.close()


@pytest.mark.parametrize("variant", [1.0, 2.0, 3.0, 4.0])
def test_parent_rejects_corrupt_sampling_window_protocol(tmp_path, variant):
    with _open(tmp_path, _bad_sampling_worker, request=_presence_request(variant)) as stream:
        with pytest.raises(VideoDecodeError, match="sampling window contract"):
            list(stream)
        assert not stream.summary.scan_complete
    assert not multiprocessing.active_children()


@pytest.mark.parametrize("pts,source", [([0.0] * 5, "estimated"), ([float("nan")] * 5, "estimated"),
                                        ([0, .25, .2, .8, 1], "mixed")])
def test_new_request_preserves_repaired_timestamp_source(tmp_path, monkeypatch, pts, source):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=5, pts=pts), request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    assert messages[-1][1].timestamp_source == source


def test_new_request_budget_exhaustion_does_not_skip_unsampled_validation(tmp_path, monkeypatch):
    capture = _Capture(actual=10)
    capture.frames[5] = np.zeros((3, 4, 3), np.float32)
    messages = _run_worker(tmp_path, monkeypatch, capture, _limits(max_analyzed_frames=1), request=_presence_request())
    assert messages[-1][:2] == ("error", ReasonCode.VIDEO_DECODE_FAILED.value)


@pytest.mark.parametrize("count,fps,last_pts", [
    (219, 29.00312250765912, 7.517644444444445),
    (241, 28.865002548477634, 8.315944444444446),
])
def test_vfr_nominal_last_frame_duration_does_not_invalidate_covered_pts(tmp_path, monkeypatch, count, fps, last_pts):
    # Anonymous field regressions: average container FPS differs from the final
    # frame cadence by 1-2 ms, but the real terminal PTS is inside the plan.
    pts = [i * last_pts / (count - 1) for i in range(count)]
    capture = _Capture(actual=count, fps=fps, pts=pts)
    messages = _run_worker(tmp_path, monkeypatch, capture, request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    summary = messages[-1][1]
    assert summary.duration_seconds > count / fps
    assert summary.scan_complete is True
    frames = [m for m in messages if m[0] == "frame"]
    assert frames[-1][1] == count - 1
    assert frames[-1][5] == "base"


def test_metadata_underestimate_within_allocated_window_keeps_tail_candidates(tmp_path, monkeypatch):
    # D=2.25 estimates five .5-second windows. All targets through 2.5 are
    # already budgeted; the separately reserved real terminal frame is at 2.5.
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=11, reported=9), request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    frames = [m for m in messages if m[0] == "frame"]
    assert [(m[1], m[4], m[5]) for m in frames[-3:]] == [
        (8, 4, "base"), (9, 4, "supplemental"), (10, 5, "base"),
    ]
    assert messages[-1][1].scan_complete is True


def test_real_terminal_pts_beyond_allocated_window_horizon_stays_incomplete(tmp_path, monkeypatch):
    messages = _run_worker(tmp_path, monkeypatch, _Capture(actual=12, reported=9), request=_presence_request())
    assert messages[-1][0] == "done", messages[-1]
    assert messages[-1][1].scan_complete is False
    frames = [m for m in messages if m[0] == "frame"]
    assert frames[-1][1] == 11
    assert frames[-1][5] == "base"
    assert not any(m[1] == 10 for m in frames)


def _vfr_covered_worker(connection, *_args):
    api = importlib.import_module("face_algorithm.video.presence_decoder")
    fps = 29.00312250765912
    terminal = 7.517644444444445
    connection.send(("metadata", VideoMetadata(4, 3, fps, 219, 219 / fps, "synthetic")))
    connection.send(("frame", 0, 0.0, np.zeros((3, 4, 3), np.uint8), 0, "base"))
    connection.send(("frame", 218, terminal, np.zeros((3, 4, 3), np.uint8), 15, "base"))
    connection.send(("done", api.PresenceDecodeSummary(219, 2, True, terminal + 1 / fps, "pts")))
    connection.close()


def test_parent_accepts_covered_real_pts_despite_nominal_duration_metadata_drift(tmp_path):
    with _open(tmp_path, _vfr_covered_worker, request=_presence_request()) as stream:
        assert len(list(stream)) == 2
        assert stream.summary.scan_complete is True
        assert stream.summary.duration_seconds > stream.metadata.duration_seconds
    assert not multiprocessing.active_children()
