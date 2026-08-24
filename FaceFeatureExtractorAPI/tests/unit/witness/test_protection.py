from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from face_algorithm.schemas import CardRegion, FaceBox, LandmarkPoint, ReasonCode
from face_algorithm.witness.blur import _WitnessBlurError
from face_algorithm.witness.protection import (
    _build_witness_protection_mask,
    _holes,
)


FACE = FaceBox(x1=80.0, y1=30.0, x2=120.0, y2=80.0, confidence=0.99)


def _options(**updates):
    values = {
        "mask_threshold": 0.50,
        "subject_edge_alpha": 0.15,
        "card_margin_ratio": 0.0,
        "feather_radius_ratio": 0.0,
        "max_single_hole_area_ratio": 0.05,
        "max_total_hole_area_ratio": 0.08,
        "max_envelope_added_image_ratio": 0.08,
        "max_envelope_added_subject_ratio": 0.25,
        "max_person_protection_ratio": 0.85,
        "max_final_protection_ratio": 0.92,
        "max_protection_card_count": 3,
        "max_protection_card_area_ratio": 0.12,
        "max_total_protection_card_area_ratio": 0.20,
        "min_protection_card_contact_ratio": 0.20,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _subject() -> np.ndarray:
    matte = np.zeros((200, 200), np.float32)
    matte[10:195, 45:155] = 1.0
    return matte


def _card(x1: int, y1: int, x2: int, y2: int, confidence=0.9) -> CardRegion:
    return CardRegion(
        corners=tuple(
            LandmarkPoint(x=float(x), y=float(y))
            for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        ),
        confidence=confidence,
    )


def test_small_enclosed_hole_is_filled_but_large_hole_is_not() -> None:
    matte = _subject()
    matte[100:120, 90:110] = 0.0  # 1%
    matte[125:175, 70:115] = 0.0  # 5.625%

    result = _build_witness_protection_mask(matte, FACE, (), _options())

    assert result.matte[110, 100] == pytest.approx(1.0)
    assert result.matte[145, 90] == pytest.approx(0.0)


def test_hole_total_and_added_area_budgets_keep_larger_holes_as_background() -> None:
    matte = np.zeros((200, 200), np.float32)
    matte[5:195, 5:195] = 1.0
    matte[20:60, 20:60] = 0.0  # 4%
    matte[80:120, 20:60] = 0.0  # 4%
    matte[140:160, 20:60] = 0.0  # 2%

    result = _build_witness_protection_mask(
        matte,
        FACE,
        (),
        _options(max_person_protection_ratio=0.90),
    )

    assert result.matte[150, 40] == pytest.approx(1.0)
    assert result.matte[40, 40] == pytest.approx(1.0)
    assert result.matte[100, 40] == pytest.approx(0.0)


def test_many_tiny_holes_use_one_label_image_not_per_hole_masks() -> None:
    core = np.ones((200, 200), dtype=np.uint8)
    core[2:198:4, 2:198:4] = 0

    labels, label_count, holes = _holes(core, _options())

    assert len(holes) == 49 * 49
    assert label_count == len(holes) + 1  # label zero plus every enclosed hole
    assert labels.nbytes == core.size * np.dtype(np.int32).itemsize
    assert all(not isinstance(hole, np.ndarray) for hole in holes)


def test_small_closing_bridges_hand_card_gap_with_bounded_added_area() -> None:
    matte = np.zeros((200, 200), np.float32)
    matte[20:190, 50:145] = 1.0
    # A three-pixel slit stays connected to external background, so only the
    # approved small closing operation (not hole filling) can repair it.
    matte[110:113, 50:107] = 0.0

    result = _build_witness_protection_mask(matte, FACE, (), _options())

    assert 2 <= result.closing_radius <= 12
    assert result.matte[111, 100] == pytest.approx(1.0)
    assert result.added_area_ratio <= 0.08
    assert float(np.mean(result.matte)) <= 0.85


def test_protection_cards_require_contact_and_obey_count_and_area_budgets() -> None:
    matte = _subject()
    touching_high = _card(135, 115, 175, 155, 0.95)
    touching_low = _card(135, 50, 175, 90, 0.70)
    detached = _card(0, 0, 30, 30, 0.99)
    fourth = _card(135, 160, 175, 195, 0.60)

    result = _build_witness_protection_mask(
        matte,
        FACE,
        (touching_high, touching_low, detached, fourth),
        _options(),
    )

    assert detached not in result.cards
    assert len(result.cards) <= 3
    assert touching_high in result.cards
    assert float(np.mean(result.matte)) <= 0.92


def test_card_partly_outside_person_is_protected_when_contact_is_sufficient() -> None:
    matte = _subject()
    card = _card(135, 115, 180, 160)

    result = _build_witness_protection_mask(matte, FACE, (card,), _options())

    assert result.cards == (card,)
    assert result.matte[130, 170] == pytest.approx(1.0)


def test_person_over_85_percent_or_near_full_soft_matte_fails_closed() -> None:
    exact = np.zeros((200, 200), np.float32)
    exact[:170, :] = 1.0
    over = exact.copy()
    over[170, :] = 1.0

    result = _build_witness_protection_mask(exact, None, (), _options())
    assert result.protected_area_ratio == pytest.approx(0.85)

    with pytest.raises(_WitnessBlurError) as caught:
        _build_witness_protection_mask(over, None, (), _options())

    assert caught.value.reason_code is ReasonCode.MATTING_LOW_CONFIDENCE


def test_final_92_percent_budget_drops_low_confidence_cards_first() -> None:
    matte = np.zeros((200, 200), np.float32)
    matte[5:195, 10:185] = 1.0  # 83.125%
    high = _card(0, 20, 25, 170, 0.95)
    low = _card(175, 20, 199, 170, 0.55)

    result = _build_witness_protection_mask(
        matte,
        FACE,
        (low, high),
        _options(min_protection_card_contact_ratio=0.05),
    )

    assert float(np.mean(result.matte)) <= 0.92
    assert high in result.cards
    if len(result.cards) == 1:
        assert result.cards == (high,)


def test_dilation_for_contact_uses_same_radius_as_closing(monkeypatch) -> None:
    matte = _subject()
    card = _card(158, 100, 180, 140)
    seen: list[tuple[int, int]] = []
    original = cv2.dilate

    def capture(source, kernel, *args, **kwargs):
        seen.append(kernel.shape)
        return original(source, kernel, *args, **kwargs)

    monkeypatch.setattr(cv2, "dilate", capture)

    result = _build_witness_protection_mask(matte, FACE, (card,), _options())

    expected = result.closing_radius * 2 + 1
    assert seen and all(shape == (expected, expected) for shape in seen)
