"""The vectorized pre/post-processing must reproduce the original loops bit for bit.

Features stored on the platform were produced by the loop versions; any drift here would change
every enrolment score. Synthetic tensors only; no ONNX models are loaded.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.facedetector import FaceBoxesV2
from models.facelandmarks5er import shape_index_process, shape_index_process_reference


@pytest.mark.parametrize("seed", range(20))
def test_shape_index_matches_reference(seed):
    rng = np.random.default_rng(seed)
    feat = rng.standard_normal((1, 8, 28, 28)).astype(np.float32)
    # Include landmarks near and past the borders so the zero-padding path is exercised.
    pos = rng.uniform(-0.1, 1.1, (1, 10)).astype(np.float32)
    expected = shape_index_process_reference(feat, pos)
    actual = shape_index_process(feat, pos)
    assert actual.dtype == expected.dtype and actual.shape == expected.shape
    assert np.array_equal(actual, expected)


def detector_without_session(size=160):
    detector = FaceBoxesV2.__new__(FaceBoxesV2)
    detector.input_node_dims = [1, 3, size, size]
    detector.variance = [0.1, 0.2]
    detector.steps = [32, 64, 128]
    detector.min_sizes = [[32, 64, 128], [256], [512]]
    detector.max_nms = 30000
    detector._anchors_f32 = np.asarray(detector.generate_anchors(size, size)).astype(np.float32)
    return detector


@pytest.mark.parametrize("seed", range(10))
def test_box_decode_matches_reference(seed):
    detector = detector_without_session()
    n = detector._anchors_f32.shape[0]
    rng = np.random.default_rng(seed)
    deltas = rng.standard_normal((1, n, 4)).astype(np.float32)
    scores = rng.uniform(0, 1, n).astype(np.float32)
    probs = np.stack([1 - scores, scores], axis=-1)[None]
    expected = detector.generate_bboxes_reference([deltas, probs], 0.35, 480.0, 640.0)
    actual = detector.generate_bboxes([deltas, probs], 0.35, 480.0, 640.0)
    assert len(actual) == len(expected) > 0
    for a, e in zip(actual, expected):
        assert (a.x1, a.y1, a.x2, a.y2, a.score) == (e.x1, e.y1, e.x2, e.y2, e.score)
        assert type(a.x1) is type(e.x1)


def test_box_decode_respects_nms_cap():
    detector = detector_without_session()
    detector.max_nms = 5
    n = detector._anchors_f32.shape[0]
    probs = np.stack([np.zeros(n), np.ones(n)], axis=-1)[None].astype(np.float32)
    deltas = np.zeros((1, n, 4), np.float32)
    assert len(detector.generate_bboxes([deltas, probs], 0.35, 100.0, 100.0)) == \
        len(detector.generate_bboxes_reference([deltas, probs], 0.35, 100.0, 100.0)) == 6
