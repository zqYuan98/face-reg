from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.schemas import FaceBox, ReasonCode
from face_algorithm.witness.blur import _WitnessBlurError
from face_algorithm.witness.subject_selector import _analyze_witness_subject


FACE = FaceBox(x1=70.0, y1=25.0, x2=130.0, y2=95.0, confidence=0.99)


def _options(**updates):
    values = {
        "mask_threshold": 0.50,
        "subject_edge_alpha": 0.15,
        "min_subject_area_ratio": 0.03,
        "max_subject_area_ratio": 0.92,
        "face_component_min_coverage": 0.50,
        "secondary_subject_area_ratio": 0.35,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _matte() -> np.ndarray:
    matte = np.zeros((200, 200), np.float32)
    matte[10:195, 45:155] = 1.0
    return matte


def test_anchored_subject_requires_one_component_covering_half_the_face() -> None:
    selected = _analyze_witness_subject(_matte(), FACE, _options())

    assert selected.anchored is True
    assert selected.component_count == 1
    assert selected.quality.face_coverage == pytest.approx(1.0)
    assert selected.matte[100, 100] == pytest.approx(1.0)

    missed = _matte()
    missed[:, :] = 0.0
    missed[25:95, 70:99] = 1.0  # 29/60 of the face width: below 50%.
    with pytest.raises(_WitnessBlurError) as caught:
        _analyze_witness_subject(missed, FACE, _options())
    assert caught.value.reason_code is ReasonCode.MATTING_LOW_CONFIDENCE


def test_two_components_that_each_cover_half_the_face_fail_as_fragmented() -> None:
    matte = np.zeros((200, 200), np.float32)
    matte[10:180, 40:100] = 1.0
    matte[10:180, 101:161] = 1.0

    with pytest.raises(_WitnessBlurError) as caught:
        _analyze_witness_subject(
            matte, FACE, _options(face_component_min_coverage=0.48)
        )

    assert caught.value.reason_code is ReasonCode.MATTING_LOW_CONFIDENCE


def test_comparable_second_reliable_subject_is_rejected() -> None:
    matte = _matte()
    matte[:, 45:155] = 0.0
    matte[10:195, 15:105] = 1.0  # anchored main component
    matte[25:195, 120:174] = 1.0  # 55.1% of the main area

    with pytest.raises(_WitnessBlurError) as caught:
        _analyze_witness_subject(matte, FACE, _options())

    assert caught.value.reason_code is ReasonCode.MULTIPLE_FACES


def test_small_background_component_is_ignored_after_anchor_selection() -> None:
    matte = _matte()
    matte[140:180, 5:25] = 1.0

    selected = _analyze_witness_subject(matte, FACE, _options())

    assert selected.component_count == 1  # tiny component is below reliable area
    assert selected.matte[150, 10] == pytest.approx(0.0)


def test_no_face_selects_only_dominant_reliable_component() -> None:
    matte = np.zeros((200, 200), np.float32)
    matte[10:190, 20:120] = 1.0
    matte[120:190, 145:175] = 1.0  # 11.7% of main component

    selected = _analyze_witness_subject(matte, None, _options())

    assert selected.anchored is False
    assert selected.component_count == 2
    assert selected.quality.face_mean == pytest.approx(0.0)
    assert selected.matte[100, 60] == pytest.approx(1.0)
    assert selected.matte[150, 155] == pytest.approx(0.0)


def test_no_face_comparable_components_are_ambiguous() -> None:
    matte = np.zeros((200, 200), np.float32)
    matte[10:190, 10:90] = 1.0
    matte[10:190, 110:180] = 1.0

    with pytest.raises(_WitnessBlurError) as caught:
        _analyze_witness_subject(matte, None, _options())

    assert caught.value.reason_code is ReasonCode.MULTIPLE_FACES


def test_no_reliable_or_near_full_subject_fails_closed() -> None:
    tiny = np.zeros((200, 200), np.float32)
    tiny[10:20, 10:20] = 1.0
    near_full = np.ones((200, 200), np.float32)
    near_full[0:10, 0:10] = 0.0

    for matte in (tiny, near_full):
        with pytest.raises(_WitnessBlurError) as caught:
            _analyze_witness_subject(matte, None, _options())
        assert caught.value.reason_code is ReasonCode.MATTING_LOW_CONFIDENCE
