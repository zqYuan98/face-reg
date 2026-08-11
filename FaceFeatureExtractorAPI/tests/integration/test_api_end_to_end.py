from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_default_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "source" / "synthetic_person_holding_blank_card.png"
FIXTURE_SHA256 = "70d1518bbb32ef8c38792870a0dbb9e1a05466354d3f5cbfc0cc470e4b0c5773"


def _assert_no_biometric_array(value) -> None:
    if isinstance(value, dict):
        assert {"feature", "embedding", "alpha", "image", "raw_image"}.isdisjoint(value)
        for child in value.values():
            _assert_no_biometric_array(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_biometric_array(child)


@pytest.mark.integration
def test_default_app_real_models_are_ready_and_uncalibrated_api_never_passes() -> None:
    media = FIXTURE.read_bytes()
    assert hashlib.sha256(media).hexdigest() == FIXTURE_SHA256

    with TestClient(create_default_app()) as client:
        assert client.get("/health/live").status_code == 200
        readiness = client.get("/health/ready")
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": ("reference.png", media, "image/png"),
                "probe_image": ("probe.png", media, "image/png"),
            },
        )
        legacy = client.post(
            "/api/extract_feature",
            files={"image": ("image.png", media, "image/png")},
        )

    assert readiness.status_code == 200
    assert readiness.json()["status"] == "degraded"
    assert readiness.json()["ready"] is True
    assert response.status_code == 200
    body = response.json()
    assert body["suggestion"] == "REVIEW"
    assert body["reason_codes"] == ["THRESHOLD_UNCALIBRATED"]
    assert body["feature_dimension"] == 1024
    _assert_no_biometric_array(body)
    assert legacy.status_code == 404
