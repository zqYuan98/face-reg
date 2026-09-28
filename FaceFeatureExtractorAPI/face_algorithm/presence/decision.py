"""Pure trial-threshold decisions; never a business approval or liveness result."""

from enum import Enum


class PresenceDecision(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND_IN_SCAN = "NOT_FOUND_IN_SCAN"
    INCONCLUSIVE = "INCONCLUSIVE"


def decide_presence(*, success: bool, scan_complete: bool, comparison_status: str,
                    best_score: float | None, threshold: float | None,
                    candidate_limit_hit: bool, failed_candidates: int
                    ) -> tuple[PresenceDecision, bool | None]:
    """Apply the ordered state table to already-validated observations."""
    if (not success or not scan_complete or comparison_status != "COMPARED"
            or best_score is None or threshold is None):
        return PresenceDecision.INCONCLUSIVE, None
    if best_score >= threshold:
        return PresenceDecision.FOUND, True
    if candidate_limit_hit or failed_candidates:
        return PresenceDecision.INCONCLUSIVE, None
    return PresenceDecision.NOT_FOUND_IN_SCAN, False
