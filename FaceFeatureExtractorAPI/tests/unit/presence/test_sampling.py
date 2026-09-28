from __future__ import annotations

import importlib
import math
from dataclasses import FrozenInstanceError

import numpy as np
import pytest


def _api():
    spec = importlib.util.find_spec("face_algorithm.presence.sampling")
    assert spec is not None, "presence requires its own bounded sampling contracts"
    return importlib.import_module(spec.name)


def test_presence_request_and_sample_are_independent_immutable_contracts():
    api = _api()
    request = api.PresenceDecodeRequest(.5)
    assert request.sample_interval_seconds == .5
    assert request.max_supplemental_frames == 2
    image = np.zeros((3, 4, 3), np.uint8)
    sample = api.PresenceSampleFrame(3, .3, image, 0, "supplemental")
    assert sample.image is image
    assert sample.kind == "supplemental"
    with pytest.raises(FrozenInstanceError):
        sample.kind = "base"


@pytest.mark.parametrize("interval", [0, -.5, True, "0.5", float("nan"), float("inf")])
def test_presence_request_rejects_invalid_interval(interval):
    with pytest.raises((TypeError, ValueError)):
        _api().PresenceDecodeRequest(interval)


@pytest.mark.parametrize("count", [-1, 3, True, 1.5, "2"])
def test_presence_request_rejects_invalid_supplemental_count(count):
    with pytest.raises((TypeError, ValueError)):
        _api().PresenceDecodeRequest(.5, count)


@pytest.mark.parametrize("budget, supplemental, windows", [(1, 0, 1), (2, 0, 1), (3, 1, 1), (4, 2, 1), (7, 2, 2)])
def test_small_budget_reserves_last_and_reduces_supplemental(budget, supplemental, windows):
    api = _api()
    plan = api.build_presence_sampling_plan(api.PresenceDecodeRequest(.5), 10.0, budget)
    assert plan.max_supplemental_frames == supplemental
    assert plan.window_count == windows
    assert plan.sampling_interval_seconds == pytest.approx(10.0 / windows)
    assert plan.frame_budget == budget


def test_long_video_plan_covers_whole_timeline_with_worst_case_budget():
    api = _api()
    plan = api.build_presence_sampling_plan(api.PresenceDecodeRequest(.5), 600.0, 1200)
    assert plan.window_count == 399
    assert plan.sampling_interval_seconds == pytest.approx(600 / 399)
    assert plan.window_count * 3 + 1 <= plan.frame_budget
    assert plan.sampling_interval_seconds * plan.window_count >= 600.0


def test_explicit_resource_budget_is_respected():
    api = _api()
    plan = api.build_presence_sampling_plan(api.PresenceDecodeRequest(.01), 600.0, 9000)
    assert plan.frame_budget == 9000
    assert plan.window_count * (1 + plan.max_supplemental_frames) + 1 <= 9000


@pytest.mark.parametrize("duration", [0.0001, .5, 1.0, 1.5000000001, 599.9999999999, 600.0])
@pytest.mark.parametrize("budget", [2, 3, 4, 5, 13, 1200])
def test_plans_never_overspend_at_float_window_boundaries(duration, budget):
    api = _api()
    plan = api.build_presence_sampling_plan(api.PresenceDecodeRequest(.5), duration, budget)
    assert math.isfinite(plan.sampling_interval_seconds)
    assert plan.sampling_interval_seconds >= .5
    assert plan.window_count * plan.sampling_interval_seconds >= duration
    assert plan.window_count * (1 + plan.max_supplemental_frames) + 1 <= budget


@pytest.mark.parametrize("duration,budget", [(0, 4), (float("nan"), 4), (True, 4), (1.0, 0), (1.0, True), (1.0, 1.5)])
def test_planner_rejects_invalid_metadata_or_budget(duration, budget):
    api = _api()
    with pytest.raises((TypeError, ValueError)):
        api.build_presence_sampling_plan(api.PresenceDecodeRequest(.5), duration, budget)
