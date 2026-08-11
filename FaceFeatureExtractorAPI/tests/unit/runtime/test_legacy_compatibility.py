from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from face_algorithm.runtime.legacy_compatibility import (
    load_legacy_compatibility,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RECOGNIZER_SHA = "ecdbd21b1a9fb637b9780e9bd25cc1818fa3f16f54f8871f8c8658791b48bc37"
ACCEPTED_PROVENANCE = "face_recognizer.onnx:verified-export-v1"


def _compatibility_payload() -> dict:
    return {
        "manifest_version": "2026-08-04-v1",
        "feature_version": "recognizer_v1_1024",
        "feature_dimension": 1024,
        "recognizer": {
            "name": "face_recognizer",
            "sha256": RECOGNIZER_SHA,
        },
        "accepted_provenance": [ACCEPTED_PROVENANCE],
        "models_manifest": "models.manifest.json",
    }


def _write_manifest_pair(tmp_path: Path) -> tuple[Path, Path]:
    models_path = tmp_path / "models.manifest.json"
    models_path.write_bytes((PROJECT_ROOT / "models.manifest.json").read_bytes())
    compatibility_path = tmp_path / "legacy-compatibility.manifest.json"
    compatibility_path.write_text(
        json.dumps(_compatibility_payload()),
        encoding="utf-8",
    )
    return compatibility_path, models_path


def test_repository_compatibility_manifest_is_strict_frozen_and_cross_checked() -> None:
    compatibility = load_legacy_compatibility(
        PROJECT_ROOT / "legacy-compatibility.manifest.json"
    )

    assert compatibility.manifest_version == "2026-08-04-v1"
    assert compatibility.feature_version.value == "recognizer_v1_1024"
    assert compatibility.feature_dimension == 1024
    assert compatibility.recognizer.name == "face_recognizer"
    assert compatibility.recognizer.sha256 == RECOGNIZER_SHA
    assert compatibility.accepted_provenance == (ACCEPTED_PROVENANCE,)
    assert compatibility.models_manifest == (
        PROJECT_ROOT / "models.manifest.json"
    ).resolve()
    with pytest.raises(ValidationError):
        compatibility.feature_dimension = 512


@pytest.mark.parametrize(
    "drift",
    ["unknown_field", "manifest_version", "non_array_provenance", "duplicate_provenance"],
)
def test_compatibility_manifest_rejects_schema_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    compatibility_path, _ = _write_manifest_pair(tmp_path)
    payload = _compatibility_payload()
    if drift == "unknown_field":
        payload["unexpected"] = True
    elif drift == "manifest_version":
        payload["manifest_version"] = "test-v1"
    elif drift == "non_array_provenance":
        payload["accepted_provenance"] = ACCEPTED_PROVENANCE
    elif drift == "duplicate_provenance":
        payload["accepted_provenance"] = [ACCEPTED_PROVENANCE, ACCEPTED_PROVENANCE]
    compatibility_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_legacy_compatibility(compatibility_path)


def test_compatibility_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    compatibility_path, _ = _write_manifest_pair(tmp_path)
    text = json.dumps(_compatibility_payload())
    compatibility_path.write_text(
        text[:-1] + ',"feature_dimension":1024}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_legacy_compatibility(compatibility_path)


def test_models_manifest_reference_must_be_relative(tmp_path: Path) -> None:
    compatibility_path, models_path = _write_manifest_pair(tmp_path)
    payload = _compatibility_payload()
    payload["models_manifest"] = str(models_path.resolve())
    compatibility_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must be relative"):
        load_legacy_compatibility(compatibility_path)


def test_compatibility_sha_drift_is_rejected(tmp_path: Path) -> None:
    compatibility_path, _ = _write_manifest_pair(tmp_path)
    payload = _compatibility_payload()
    payload["recognizer"]["sha256"] = "f" * 64
    compatibility_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="recognizer SHA"):
        load_legacy_compatibility(compatibility_path)


def test_models_manifest_sha_drift_is_rejected(tmp_path: Path) -> None:
    compatibility_path, models_path = _write_manifest_pair(tmp_path)
    models = json.loads(models_path.read_text(encoding="utf-8"))
    recognizer = next(
        model for model in models["models"] if model["name"] == "face_recognizer"
    )
    recognizer["sha256"] = "e" * 64
    models_path.write_text(json.dumps(models), encoding="utf-8")

    with pytest.raises(ValueError, match="recognizer SHA"):
        load_legacy_compatibility(compatibility_path)
