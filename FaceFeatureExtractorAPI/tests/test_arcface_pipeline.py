"""Geometry of the arcface_r50_512 pipeline; no ONNX models are loaded."""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.arcface_pipeline import ARCFACE_DST, execution_providers, norm_crop, pose5, umeyama_similarity


def pose5_reference(p):
    """Line-by-line port of android face-core engine/Pose5.java."""
    ex, ey = p[2] - p[0], p[3] - p[1]
    iod = math.hypot(ex, ey)
    roll = math.atan2(ey, ex)
    c, s = math.cos(-roll), math.sin(-roll)
    ox, oy = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
    r = []
    for k in range(5):
        dx, dy = p[2 * k] - ox, p[2 * k + 1] - oy
        r += [dx * c - dy * s, dx * s + dy * c]
    mouth_x, mouth_y = (r[6] + r[8]) / 2, (r[7] + r[9]) / 2
    midline = mouth_x * (r[5] / mouth_y)
    return (r[4] - midline) / iod * -72.8, (r[5] / mouth_y - 0.4609) * -53.7, math.degrees(roll)


@pytest.mark.parametrize("seed", range(10))
def test_pose_matches_device_formula(seed):
    rng = np.random.default_rng(seed)
    angle, scale = rng.uniform(-0.5, 0.5), rng.uniform(1, 4)
    rot = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    kps = (ARCFACE_DST + rng.normal(0, 2, (5, 2))) @ rot.T * scale + rng.uniform(0, 300, 2)
    assert np.allclose(pose5(kps), pose5_reference(kps.reshape(-1)), atol=1e-9)


def test_frontal_template_is_frontal_and_degenerate_points_are_nan():
    yaw, pitch, roll = pose5(ARCFACE_DST)
    assert abs(yaw) < 5 and abs(pitch) < 5 and abs(roll) < 1
    assert all(math.isnan(v) for v in pose5(np.zeros((5, 2))))


def test_alignment_maps_keypoints_onto_the_template():
    rng = np.random.default_rng(3)
    kps = ARCFACE_DST * 2.5 + np.array([100.0, 60.0])
    m = umeyama_similarity(kps, ARCFACE_DST)
    mapped = np.hstack([kps, np.ones((5, 1))]) @ m.T
    assert np.allclose(mapped, ARCFACE_DST, atol=1e-4)
    image = rng.integers(0, 255, (400, 400, 3), np.uint8)
    assert norm_crop(image, kps).shape == (112, 112, 3)


def test_unknown_or_unavailable_device_is_an_error():
    assert execution_providers("cpu") == ["CPUExecutionProvider"]
    with pytest.raises(ValueError):
        execution_providers("npu")
