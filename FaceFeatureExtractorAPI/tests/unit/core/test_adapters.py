from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import face_algorithm.core.adapters as adapters
from face_algorithm.core.adapters import (
    FaceBoxesAdapter,
    FaceRecognizerAdapter,
    RotationAdapter,
)
from face_algorithm.core.face_selection import FaceCandidateBatch
from models.facedetector import Box


class FakeRawDetector:
    def __init__(self, boxes) -> None:
        self.boxes = boxes
        self.kwargs = None

    def detect(self, *_args, **_kwargs):
        self.kwargs = _kwargs
        return self.boxes


def _face_boxes_adapter(monkeypatch, boxes) -> FaceBoxesAdapter:
    detector = FakeRawDetector(boxes)
    monkeypatch.setattr(adapters, "FaceBoxesV2", lambda session: detector)
    return FaceBoxesAdapter(object())


def test_face_boxes_adapter_preserves_finite_negative_coordinates(monkeypatch) -> None:
    adapter = _face_boxes_adapter(
        monkeypatch,
        [Box(-20.0, -10.0, 80.0, 90.0, 0.8)],
    )

    result = adapter.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert len(result) == 1
    assert result[0].x1 == -20.0
    assert result[0].y1 == -10.0


def test_face_boxes_adapter_requests_five_candidates_plus_saturation_slot(
    monkeypatch,
) -> None:
    detector = FakeRawDetector([])
    monkeypatch.setattr(adapters, "FaceBoxesV2", lambda session: detector)
    adapter = FaceBoxesAdapter(object())

    adapter.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert adapter.max_faces_to_detect == 6
    assert detector.kwargs["topk"] > 6


def test_face_boxes_adapter_filters_fully_outside_boxes_before_public_cap(
    monkeypatch,
) -> None:
    inside = Box(10.0, 10.0, 80.0, 80.0, 0.9)
    outside = [
        Box(-200.0 - index * 20.0, 10.0, -100.0 - index * 20.0, 80.0, 0.99)
        for index in range(5)
    ]
    adapter = _face_boxes_adapter(monkeypatch, [inside, *outside])

    result = adapter.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert isinstance(result, FaceCandidateBatch)
    assert len(result) == 1
    assert result[0].x1 == inside.x1
    assert result.saturated is False


class TopKRawDetector(FakeRawDetector):
    def detect(self, *_args, **_kwargs):
        self.kwargs = _kwargs
        return self.boxes[: _kwargs["topk"]]


def test_face_boxes_adapter_scans_beyond_six_before_filtering(
    monkeypatch,
) -> None:
    first = Box(10.0, 10.0, 80.0, 80.0, 0.99)
    outside = [
        Box(-200.0 - index * 20.0, 10.0, -100.0 - index * 20.0, 80.0, 0.98)
        for index in range(5)
    ]
    seventh_valid = Box(15.0, 15.0, 90.0, 90.0, 0.80)
    detector = TopKRawDetector([first, *outside, seventh_valid])
    monkeypatch.setattr(adapters, "FaceBoxesV2", lambda session: detector)
    adapter = FaceBoxesAdapter(object())

    result = adapter.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert detector.kwargs["topk"] > 6
    assert len(result) == 2
    assert [box.confidence for box in result] == [0.99, 0.80]
    assert result.saturated is False


def test_face_boxes_adapter_marks_unresolved_raw_scan_saturation(
    monkeypatch,
) -> None:
    detector = TopKRawDetector([])
    monkeypatch.setattr(adapters, "FaceBoxesV2", lambda session: detector)
    adapter = FaceBoxesAdapter(object())
    inside = Box(10.0, 10.0, 80.0, 80.0, 0.99)
    detector.boxes = [inside] + [
        Box(-200.0 - index * 20.0, 10.0, -100.0 - index * 20.0, 80.0, 0.98)
        for index in range(adapter.raw_candidate_scan_limit)
    ]

    result = adapter.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert len(result) == 1
    assert result.saturated is True


def test_face_boxes_adapter_does_not_mark_an_exhausted_full_scan_as_saturated(
    monkeypatch,
) -> None:
    detector = TopKRawDetector([])
    monkeypatch.setattr(adapters, "FaceBoxesV2", lambda session: detector)
    adapter = FaceBoxesAdapter(object())
    inside = Box(10.0, 10.0, 80.0, 80.0, 0.99)
    detector.boxes = [inside] + [
        Box(-200.0 - index * 20.0, 10.0, -100.0 - index * 20.0, 80.0, 0.98)
        for index in range(adapter.raw_candidate_scan_limit - 1)
    ]

    result = adapter.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert len(result) == 1
    assert result.saturated is False


def test_face_boxes_adapter_rejects_unbounded_candidate_limit(monkeypatch) -> None:
    monkeypatch.setattr(adapters, "FaceBoxesV2", lambda session: FakeRawDetector([]))

    with pytest.raises(ValueError, match="cannot exceed six"):
        FaceBoxesAdapter(object(), max_faces_to_detect=7)


@pytest.mark.parametrize("invalid_limit", [True, 6.0, "6"])
def test_face_boxes_adapter_requires_integer_candidate_limit(
    monkeypatch,
    invalid_limit,
) -> None:
    monkeypatch.setattr(adapters, "FaceBoxesV2", lambda session: FakeRawDetector([]))

    with pytest.raises(TypeError, match="integer"):
        FaceBoxesAdapter(object(), max_faces_to_detect=invalid_limit)


@pytest.mark.parametrize(
    "malformed",
    [
        Box(10.0, 10.0, np.nan, 80.0, 0.8),
        Box(10.0, 10.0, 10.0, 80.0, 0.8),
        Box(10.0, 10.0, 80.0, 10.0, 0.8),
        Box(10.0, 10.0, 80.0, 80.0, np.nan),
        Box(10.0, 10.0, 80.0, 80.0, np.inf),
        Box(10.0, 10.0, 80.0, 80.0, -0.01),
        Box(10.0, 10.0, 80.0, 80.0, 1.01),
    ],
)
def test_face_boxes_adapter_rejects_any_malformed_member(monkeypatch, malformed) -> None:
    adapter = _face_boxes_adapter(
        monkeypatch,
        [Box(0.0, 0.0, 50.0, 50.0, 0.9), malformed],
    )

    with pytest.raises((TypeError, ValueError)):
        adapter.detect(np.zeros((100, 100, 3), dtype=np.uint8))


class FakeRotationSession:
    def __init__(self, scores) -> None:
        self.scores = scores

    def get_inputs(self):
        return [SimpleNamespace(name="input")]

    def get_outputs(self):
        return [SimpleNamespace(name="scores")]

    def run(self, *_args, **_kwargs):
        return [self.scores]


@pytest.mark.parametrize(
    "scores",
    [
        [[0.0, 1.0, 0.0, 0.0]],
        np.asarray([[0.0, 1.0, 0.0, 0.0]], dtype=np.float64),
        np.asarray([0.0, 1.0, 0.0, 0.0], dtype=np.float32),
        np.asarray([[[0.0], [1.0], [0.0], [0.0]]], dtype=np.float32),
        np.asarray([[0.0, np.nan, 0.0, 0.0]], dtype=np.float32),
        np.asarray([[0.0, np.inf, 0.0, 0.0]], dtype=np.float32),
    ],
)
def test_rotation_adapter_rejects_invalid_score_contract(scores) -> None:
    adapter = RotationAdapter(FakeRotationSession(scores))

    with pytest.raises((TypeError, ValueError)):
        adapter.correct(np.zeros((120, 200, 3), dtype=np.uint8))


def test_rotation_adapter_accepts_exact_finite_float32_scores() -> None:
    scores = np.asarray([[0.0, 1.0, 0.0, 0.0]], dtype=np.float32)
    adapter = RotationAdapter(FakeRotationSession(scores))
    image = np.zeros((120, 200, 3), dtype=np.uint8)

    result = adapter.correct(image)

    assert result.quarter_turns == 1
    assert result.corrected_image.shape == (200, 120, 3)


def test_face_recognizer_adapter_does_not_coerce_raw_model_output(monkeypatch) -> None:
    raw = np.ones(1024, dtype=np.float64)
    recognizer = SimpleNamespace(inference=lambda _image: raw)
    monkeypatch.setattr(adapters, "FaceRecoger", lambda session: recognizer)
    adapter = FaceRecognizerAdapter(object())

    result = adapter.extract(np.zeros((256, 256, 3), dtype=np.uint8))

    assert result is raw
    assert result.dtype == np.float64
