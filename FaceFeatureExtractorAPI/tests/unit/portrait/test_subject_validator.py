from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.portrait.subject_validator import (
    PortraitSubjectValidationError,
    validate_portrait_subject,
)
from face_algorithm.schemas import FaceBox, ReasonCode


FACE = FaceBox(x1=50.0, y1=40.0, x2=150.0, y2=100.0, confidence=0.99)


def _settings(**updates: float | int) -> SimpleNamespace:
    values: dict[str, float | int] = {
        "alpha_threshold": 0.50,
        "min_shoulder_depth_ratio": 0.60,
        "min_shoulder_band_coverage": 0.35,
        "head_roi_expansion_ratio": 0.25,
        "top_edge_band_ratio": 0.005,
        "top_edge_min_pixels": 2,
        "min_top_edge_core_coverage": 0.15,
        "robust_top_min_row_width_ratio": 0.05,
        "required_headroom_ratio": 0.12,
        "shoulder_roi_width_ratio": 1.30,
        "min_shoulder_half_coverage": 0.20,
        "min_shoulder_valid_row_ratio": 0.60,
        "min_shoulder_row_coverage": 0.25,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _complete_core() -> np.ndarray:
    core = np.zeros((200, 200), dtype=np.uint8)
    core[20:200, 20:180] = 1
    return core


def test_sparse_hard_tail_is_ignored_by_robust_top() -> None:
    core = _complete_core()
    core[0:20, 100] = 1

    validation = validate_portrait_subject(core, FACE, _settings())

    assert validation.robust_core_top == 20


def test_true_head_crop_requires_hard_top_occupancy_and_insufficient_headroom() -> None:
    core = _complete_core()
    core[0:20, 20:180] = 1
    face = FACE.model_copy(update={"y1": 5.0, "y2": 65.0})

    with pytest.raises(PortraitSubjectValidationError) as captured:
        validate_portrait_subject(core, face, _settings())

    assert captured.value.reason_code is ReasonCode.SUBJECT_CROPPED


def test_hard_top_contact_with_required_headroom_is_not_a_crop() -> None:
    core = _complete_core()
    core[0:20, 20:180] = 1

    validation = validate_portrait_subject(core, FACE, _settings())

    assert validation.robust_core_top == 0


def test_top_occupancy_below_threshold_does_not_claim_a_crop() -> None:
    core = _complete_core()
    core[0:2, 70:80] = 1
    face = FACE.model_copy(update={"y1": 5.0, "y2": 65.0})

    validation = validate_portrait_subject(core, face, _settings())

    assert validation.robust_core_top == 20
    assert validation.top_edge_core_coverage < 0.15


def test_connected_hard_tail_between_row_and_edge_thresholds_is_not_placement_top() -> None:
    core = _complete_core()
    # Nine pixels satisfy ceil(5% * 100px face width), but remain far below
    # 15% of the expanded 150px head ROI and must not drive placement.
    core[0:20, 96:105] = 1

    validation = validate_portrait_subject(core, FACE, _settings())

    assert validation.robust_core_top == 20


def test_shoulder_overall_coverage_is_required() -> None:
    core = _complete_core()
    core[100:136, :] = 0
    core[100:136, 80:120] = 1

    with pytest.raises(PortraitSubjectValidationError) as captured:
        validate_portrait_subject(core, FACE, _settings())

    assert captured.value.reason_code is ReasonCode.SHOULDERS_NOT_VISIBLE


def test_both_shoulder_halves_are_required_even_when_overall_coverage_passes() -> None:
    core = _complete_core()
    core[100:136, :] = 0
    core[100:136, 100:165] = 1

    with pytest.raises(PortraitSubjectValidationError) as captured:
        validate_portrait_subject(core, FACE, _settings())

    assert captured.value.reason_code is ReasonCode.SHOULDERS_NOT_VISIBLE


def test_shoulder_coverage_must_span_enough_rows() -> None:
    core = _complete_core()
    core[100:136, :] = 0
    core[100:118, 35:165] = 1

    with pytest.raises(PortraitSubjectValidationError) as captured:
        validate_portrait_subject(core, FACE, _settings())

    assert captured.value.reason_code is ReasonCode.SHOULDERS_NOT_VISIBLE


def test_exact_shoulder_coverage_boundaries_are_inclusive() -> None:
    core = _complete_core()
    core[100:136, :] = 0
    # The first 22/36 rows cover half of each ROI half. This gives overall and
    # half coverage above their boundaries and valid-row coverage 22/36 >= 0.60.
    core[100:122, 35:165] = 1

    validation = validate_portrait_subject(core, FACE, _settings())

    assert validation.shoulder_band_coverage == pytest.approx(22.0 / 36.0)
    assert validation.shoulder_valid_row_ratio == pytest.approx(22.0 / 36.0)


def test_side_and_bottom_contact_outside_required_head_shoulders_are_allowed() -> None:
    core = _complete_core()
    core[136:200, :] = 1

    validation = validate_portrait_subject(core, FACE, _settings())

    assert validation.robust_core_top == 20
    assert validation.shoulder_band_coverage == pytest.approx(1.0)
