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
from models.facedetector import Box


class FakeRawDetector:
    def __init__(self, boxes) -> None:
        self.boxes = boxes

    def detect(self, *_args, **_kwargs):
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
