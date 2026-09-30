"""Fixed portrait-only overflow budgets use the raw box and visible source."""

from types import SimpleNamespace

import numpy as np
import pytest

from face_algorithm.core.portrait_bounds import portrait_box_within_overflow_budget
from face_algorithm.schemas import FaceBox


def _box(coordinates):
    return SimpleNamespace(**dict(zip(("x1", "y1", "x2", "y2"), coordinates)))


@pytest.mark.parametrize(
    "coordinates",
    [(0, 0, 400, 400), (100, 100, 300, 300),
     (-0.01, 100, 200, 300), (200, 100, 400.01, 300),
     (100, -0.01, 300, 200), (100, 200, 300, 400.01),
     (-10, 100, 190, 300), (210, 100, 410, 300),
     (100, -10, 300, 190), (100, 210, 300, 410),
     (-10, -10, 190, 190)],
)
def test_in_bounds_and_per_side_overflow_up_to_five_percent_are_accepted(coordinates):
    assert portrait_box_within_overflow_budget(_box(coordinates), (400, 400, 3))


@pytest.mark.parametrize(
    "coordinates",
    [(-10.000001, 100, 190, 300), (210, 100, 410.000001, 300),
     (100, -10.000001, 300, 190), (100, 210, 300, 410.000001),
     (-np.nextafter(10.0, np.inf), 100, 190, 300)],
)
def test_each_side_above_five_percent_is_rejected_without_added_tolerance(coordinates):
    assert not portrait_box_within_overflow_budget(_box(coordinates), (400, 400, 3))


@pytest.mark.parametrize(
    "shape,accepted",
    [((200, 180), True), ((200, 180, 3), True), ((199, 180, 3), False)],
)
def test_visible_area_ninety_percent_is_inclusive(shape, accepted):
    assert portrait_box_within_overflow_budget(_box((-10, 0, 190, 200)), shape) is accepted


def test_four_sides_can_each_pass_but_fail_total_visible_area():
    assert not portrait_box_within_overflow_budget(_box((-10, -10, 190, 190)), (180, 180, 3))


def test_exact_ninety_percent_area_with_overflow_on_both_axes_is_accepted():
    # 190 * 180 / (200 * 190) == 0.9; multiplying separate dimension ratios
    # rounds to 0.8999999999999999 and must not reject this inclusive boundary.
    assert portrait_box_within_overflow_budget(_box((-10, -5, 190, 185)), (180, 190, 3))


def test_reported_geometry_is_accepted_without_mutating_raw_box():
    box = FaceBox(x1=58.450283, y1=31.375793, x2=610.558960, y2=737.061401)

    assert portrait_box_within_overflow_budget(box, (720, 717, 3))
    assert box.y2 == 737.061401


@pytest.mark.parametrize(
    "coordinates",
    [(np.nan, 100, 300, 300), (100, np.inf, 300, 300),
     (100, 100, -np.inf, 300), (100, 100, 300, np.nan),
     (100, 100, 100, 300), (100, 100, 300, 100),
     (300, 100, 100, 300), (100, 300, 300, 100),
     (-200, 100, -1, 300), (401, 100, 500, 300),
     (100, -200, 300, -1), (100, 401, 300, 500),
     (-1e308, 100, 1e308, 300)],
)
def test_nonfinite_degenerate_or_invisible_boxes_are_rejected(coordinates):
    assert not portrait_box_within_overflow_budget(_box(coordinates), (400, 400, 3))


@pytest.mark.parametrize("box", [None, object(), (), SimpleNamespace(x1=0, y1=0, x2="bad", y2=10)])
def test_malformed_boxes_are_rejected(box):
    assert not portrait_box_within_overflow_budget(box, (400, 400, 3))


@pytest.mark.parametrize(
    "shape",
    [None, (), (400,), (0, 400), (400, 0), (-1, 400), (400, -1),
     (np.nan, 400), (400, np.inf), (True, 400), (400, False),
     (400.5, 400), (400, "400"), (400, 400, 0), (400, 400, 3, 1)],
)
def test_invalid_image_shapes_are_rejected(shape):
    assert not portrait_box_within_overflow_budget(_box((0, 0, 200, 200)), shape)
