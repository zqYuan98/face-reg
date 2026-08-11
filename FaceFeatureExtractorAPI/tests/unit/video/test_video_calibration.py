from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from face_algorithm.runtime.calibration import (
    CalibrationLoadError,
    CalibrationStatus,
    VideoCalibrationArtifact,
    load_video_calibration,
)
from face_algorithm.runtime.config import load_config
from face_algorithm.schemas import ReasonCode, Suggestion
from face_algorithm.verification.policy import decide_image_verification


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "algorithm.yaml"


def _load_active(bundle):
    return load_video_calibration(
        bundle.config.video_verification,
        key_store=bundle.key_store,
        model_evidence=bundle.evidence,
        quality_profiles=bundle.config.quality_profiles,
    )


def test_signed_video_calibration_activates_only_bound_thresholds(
    active_video_calibration_bundle,
) -> None:
    state = _load_active(active_video_calibration_bundle)

    assert state.status is CalibrationStatus.ACTIVE
    assert isinstance(state.artifact, VideoCalibrationArtifact)
    assert state.threshold == 0.72
    assert state.video_thresholds is not None
    assert state.video_thresholds.quick_aggregate_threshold == 0.75
    assert state.video_thresholds.quick_min_pass_ratio == 0.70
    assert state.video_thresholds.full_aggregate_threshold == 0.77
    assert state.video_thresholds.full_min_pass_ratio == 0.80


def test_video_calibration_fails_closed_when_policy_fingerprint_drifts(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    payload = yaml.safe_load(DEFAULT_CONFIG.read_text("utf-8"))
    payload["video_verification"]["calibration"] = (
        bundle.config.video_verification.calibration.model_dump(mode="json")
    )
    payload["video_verification"]["quick"]["aggregate_percentile"] = 0.20
    drift_path = tmp_path / "drift.yaml"
    drift_path.write_text(yaml.safe_dump(payload, sort_keys=False), "utf-8")
    drifted = load_config(drift_path)

    with pytest.raises(CalibrationLoadError, match="policy binding"):
        load_video_calibration(
            drifted.video_verification,
            key_store=bundle.key_store,
            model_evidence=bundle.evidence,
            quality_profiles=drifted.quality_profiles,
        )


def test_uncalibrated_video_mode_cannot_carry_thresholds() -> None:
    config = load_config(DEFAULT_CONFIG)

    state = load_video_calibration(
        config.video_verification,
        key_store=None,
        model_evidence={},
        quality_profiles=config.quality_profiles,
    )

    assert state.status is CalibrationStatus.UNCALIBRATED
    assert state.threshold is None
    assert state.video_thresholds is None
    assert state.artifact is None


def test_video_calibration_capability_cannot_activate_image_policy(
    active_video_calibration_bundle,
) -> None:
    state = _load_active(active_video_calibration_bundle)

    decision = decide_image_verification(
        reference_reason_codes=(),
        probe_reason_codes=(),
        score=1.0,
        calibration=state,
    )

    assert decision.suggestion is Suggestion.REVIEW
    assert decision.reason_codes == (ReasonCode.FEATURE_FAILED,)
