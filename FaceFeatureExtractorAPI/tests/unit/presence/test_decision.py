import pytest
from pydantic import ValidationError

from face_algorithm.presence.config import VideoPresencePolicy


def test_configured_policy_enables_explicit_trial_threshold():
    policy = VideoPresencePolicy(mode="configured", threshold=0.8, threshold_id="synthetic-only")
    assert policy.threshold == 0.8
    assert policy.max_supplemental_frames == 2


@pytest.mark.parametrize("changes", [
    {}, {"threshold": 0.8}, {"threshold": True, "threshold_id": "x"},
    {"threshold": float("nan"), "threshold_id": "x"},
    {"threshold": -1.0, "threshold_id": "x"}, {"threshold": 1.0, "threshold_id": "x"},
    {"threshold": 0.8, "threshold_id": "  "},
])
def test_configured_policy_rejects_missing_or_invalid_metadata(changes):
    with pytest.raises(ValidationError):
        VideoPresencePolicy(mode="configured", **changes)


@pytest.mark.parametrize("changes,expected", [
    ({}, "FOUND"), ({"best_score": 0.79}, "NOT_FOUND_IN_SCAN"),
    ({"best_score": 0.8}, "FOUND"),
    ({"threshold": None}, "INCONCLUSIVE"),
    ({"scan_complete": False}, "INCONCLUSIVE"),
    ({"success": False, "comparison_status": "FAILED"}, "INCONCLUSIVE"),
    ({"comparison_status": "NO_COMPARABLE_FACE", "best_score": None}, "INCONCLUSIVE"),
    ({"comparison_status": "INVALID_REFERENCE", "best_score": None}, "INCONCLUSIVE"),
    ({"candidate_limit_hit": True}, "FOUND"),
    ({"failed_candidates": 1}, "FOUND"),
    ({"best_score": 0.2, "candidate_limit_hit": True}, "INCONCLUSIVE"),
    ({"best_score": 0.2, "failed_candidates": 1}, "INCONCLUSIVE"),
])
def test_decision_state_table(changes, expected):
    from face_algorithm.presence.decision import decide_presence
    values = dict(success=True, scan_complete=True, comparison_status="COMPARED",
                  best_score=0.9, threshold=0.8, candidate_limit_hit=False, failed_candidates=0)
    decision, found = decide_presence(**(values | changes))
    assert decision.value == expected
    assert found is {"FOUND": True, "NOT_FOUND_IN_SCAN": False, "INCONCLUSIVE": None}[expected]
