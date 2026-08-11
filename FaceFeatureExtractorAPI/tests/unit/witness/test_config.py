from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from face_algorithm.runtime.config import WitnessOptions, load_config


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_default_witness_config_has_conservative_card_and_blur_bounds() -> None:
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    options = config.witness

    assert 0 < options.max_witness_pixels <= config.resources.max_image_pixels
    assert options.max_card_count == 3
    assert 128 <= options.blur_working_max_dimension <= 2048
    assert options.min_card_area_ratio == pytest.approx(0.002)
    assert options.min_card_width_to_face_ratio <= options.max_card_width_to_face_ratio
    assert options.min_card_area_to_face_ratio <= options.max_card_area_to_face_ratio
    assert 0.0 < options.min_card_rectangularity <= 1.0
    assert 0.0 <= options.max_card_angle_deviation_degrees < 90.0
    assert options.blur_kernel_cap > 0 and options.blur_kernel_cap % 2 == 1
    assert options.subject_edge_alpha <= options.mask_threshold
    assert options.min_subject_area_ratio < options.max_subject_area_ratio < 1.0
    assert options.card_detection_max_dimension >= options.min_card_short_side_pixels
    assert "require_card" not in WitnessOptions.model_fields


@pytest.mark.parametrize(
    "updates",
    [
        {"max_card_count": 0},
        {"max_card_count": 4},
        {"min_card_width_to_face_ratio": 2.0, "max_card_width_to_face_ratio": 1.0},
        {"min_card_area_to_face_ratio": 1.0, "max_card_area_to_face_ratio": 0.5},
        {"min_card_rectangularity": 0.0},
        {"max_card_angle_deviation_degrees": 90.0},
        {"subject_edge_alpha": 0.8, "mask_threshold": 0.5},
        {"min_subject_area_ratio": 0.8, "max_subject_area_ratio": 0.7},
        {"max_subject_area_ratio": 1.0},
        {"max_witness_pixels": 0},
        {"blur_working_max_dimension": 127},
        {"blur_kernel_cap": 1},
        {"blur_kernel_cap": 30},
        {"card_ambiguity_margin": 0.0},
        {"min_card_candidate_confidence": 0.5, "card_ambiguity_margin": 0.6},
        {"card_detection_max_dimension": 127},
        {"min_card_short_side_pixels": 7},
        {"max_card_center_offset_to_face_width_ratio": 0.0},
        {"card_detection_max_dimension": 128, "min_card_short_side_pixels": 129},
    ],
)
def test_invalid_witness_safety_ranges_are_rejected(updates) -> None:
    options = load_config(PROJECT_ROOT / "config" / "algorithm.yaml").witness

    with pytest.raises(ValidationError):
        WitnessOptions.model_validate(options.model_dump() | updates)


def test_card_requirement_cannot_be_disabled_by_configuration() -> None:
    options = load_config(PROJECT_ROOT / "config" / "algorithm.yaml").witness

    with pytest.raises(ValidationError):
        WitnessOptions.model_validate(options.model_dump() | {"require_card": False})


def test_exact_inclusive_card_bounds_are_valid() -> None:
    options = load_config(PROJECT_ROOT / "config" / "algorithm.yaml").witness
    payload = options.model_dump() | {
        "min_card_area_ratio": 0.01,
        "max_card_area_ratio": 0.01,
        "min_card_aspect_ratio": 1.5,
        "max_card_aspect_ratio": 1.5,
        "min_card_width_to_face_ratio": 1.0,
        "max_card_width_to_face_ratio": 1.0,
        "min_card_area_to_face_ratio": 0.2,
        "max_card_area_to_face_ratio": 0.2,
    }

    exact = WitnessOptions.model_validate(payload)

    assert exact.min_card_area_ratio == exact.max_card_area_ratio == 0.01
