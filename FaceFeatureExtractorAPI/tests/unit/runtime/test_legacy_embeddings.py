from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.check_legacy_embeddings as checker_module
from face_algorithm.runtime.legacy_compatibility import (
    load_legacy_compatibility,
)
from scripts.check_legacy_embeddings import check_legacy_embeddings
from tests.fixtures.generate_legacy_embeddings import build_records


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SMOKE_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "generated" / "legacy_embeddings.jsonl"
COMPATIBILITY_MANIFEST = PROJECT_ROOT / "legacy-compatibility.manifest.json"
COMPATIBILITY = load_legacy_compatibility(COMPATIBILITY_MANIFEST)
CURRENT_RECOGNIZER_SHA256 = COMPATIBILITY.recognizer.sha256
CURRENT_FEATURE_DIMENSION = COMPATIBILITY.feature_dimension
ACCEPTED_PROVENANCE = COMPATIBILITY.accepted_provenance[0]


def _load_fixture() -> list[dict]:
    return [
        json.loads(line)
        for line in SMOKE_FIXTURE.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _write_compatibility_pair(
    tmp_path: Path,
    *,
    accepted_provenance: str = ACCEPTED_PROVENANCE,
) -> Path:
    (tmp_path / "models.manifest.json").write_bytes(
        (PROJECT_ROOT / "models.manifest.json").read_bytes()
    )
    payload = json.loads(COMPATIBILITY_MANIFEST.read_text(encoding="utf-8"))
    payload["accepted_provenance"] = [accepted_provenance]
    destination = tmp_path / "legacy-compatibility.manifest.json"
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return destination


def test_generated_fixture_contains_normalized_vector_and_all_three_outcomes(
    tmp_path: Path,
) -> None:
    records = _load_fixture()
    compatible = records[0]
    assert compatible["model_sha256"] == CURRENT_RECOGNIZER_SHA256
    assert len(compatible["embedding"]) == CURRENT_FEATURE_DIMENSION
    assert math.isclose(
        math.sqrt(sum(value * value for value in compatible["embedding"])),
        1.0,
        rel_tol=1e-12,
    )

    report_path = tmp_path / "report.json"
    report = check_legacy_embeddings(SMOKE_FIXTURE, report_path)
    assert report["summary"] == {
        "compatible": 1,
        "requires_reextract": 1,
        "invalid": 1,
        "total": 3,
    }
    assert report["current_contract"]["accepted_provenance"] == [
        ACCEPTED_PROVENANCE
    ]
    assert report["current_contract"]["manifest_version"] == "2026-08-04-v1"
    assert report["current_contract"]["recognizer_name"] == "face_recognizer"


def test_checker_keeps_input_byte_exact_and_report_contains_no_vectors(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "records.jsonl"
    original = SMOKE_FIXTURE.read_bytes()
    input_path.write_bytes(original)
    before_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    report_path = tmp_path / "report.json"

    report = check_legacy_embeddings(input_path, report_path)

    assert input_path.read_bytes() == original
    assert hashlib.sha256(input_path.read_bytes()).hexdigest() == before_hash
    serialized = report_path.read_text(encoding="utf-8")
    assert "embedding" not in serialized.casefold()
    assert "0.03125" not in serialized
    assert report["groups"]["requires_reextract"]["records"][0][
        "can_reextract"
    ] is True
    for group_name, expected_count in (
        ("compatible", 1),
        ("requires_reextract", 1),
        ("invalid", 1),
    ):
        assert report["groups"][group_name]["count"] == expected_count


def test_checker_streams_without_path_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "records.jsonl"
    with SMOKE_FIXTURE.open("rb") as source, input_path.open("wb") as destination:
        destination.write(source.read())

    def forbid_read_bytes(_path: Path) -> bytes:
        raise AssertionError("legacy audit must not read the whole input into memory")

    monkeypatch.setattr(Path, "read_bytes", forbid_read_bytes)
    report = check_legacy_embeddings(input_path, tmp_path / "report.json")

    assert report["summary"]["total"] == 3


def test_duplicate_json_key_cannot_override_an_untrusted_model_sha(
    tmp_path: Path,
) -> None:
    normalized = [
        1.0 / math.sqrt(CURRENT_FEATURE_DIMENSION)
    ] * CURRENT_FEATURE_DIMENSION
    record = {
        "id": "duplicate-key",
        "embedding": normalized,
        "feature_dim": CURRENT_FEATURE_DIMENSION,
        "model_sha256": "f" * 64,
        "provenance": ACCEPTED_PROVENANCE,
    }
    line = json.dumps(record)[:-1] + (
        f',"model_sha256":"{CURRENT_RECOGNIZER_SHA256}"}}\n'
    )
    input_path = tmp_path / "records.jsonl"
    input_path.write_text(line, encoding="utf-8")

    report = check_legacy_embeddings(input_path, tmp_path / "report.json")

    assert report["summary"]["compatible"] == 0
    assert report["summary"]["invalid"] == 1


def test_duplicate_stable_id_is_overall_failure_and_preserves_report(
    tmp_path: Path,
) -> None:
    compatible = _load_fixture()[0]
    input_path = tmp_path / "records.jsonl"
    input_path.write_text(
        json.dumps(compatible) + "\n" + json.dumps(compatible) + "\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    report_path.write_text("sentinel-report", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate record id"):
        check_legacy_embeddings(input_path, report_path)
    assert report_path.read_text(encoding="utf-8") == "sentinel-report"


def test_input_timestamp_change_is_detected_before_report_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "records.jsonl"
    input_path.write_bytes(SMOKE_FIXTURE.read_bytes())
    report_path = tmp_path / "report.json"
    report_path.write_text("sentinel-report", encoding="utf-8")
    initial = input_path.stat()
    original_classifier = checker_module._classify_line
    changed = False

    def classify_and_touch(*args, **kwargs):
        nonlocal changed
        result = original_classifier(*args, **kwargs)
        if not changed:
            changed = True
            os.utime(
                input_path,
                ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000),
            )
        return result

    monkeypatch.setattr(checker_module, "_classify_line", classify_and_touch)

    with pytest.raises(ValueError, match="changed during audit"):
        check_legacy_embeddings(input_path, report_path)
    assert report_path.read_text(encoding="utf-8") == "sentinel-report"


def test_default_audit_limits_are_public_and_bounded() -> None:
    limits = checker_module.DEFAULT_AUDIT_LIMITS
    assert limits.max_input_bytes == 512 * 1024 * 1024
    assert limits.max_line_bytes == 256 * 1024
    assert limits.max_records == 100_000


@pytest.mark.parametrize(
    ("limit_name", "expected_message"),
    [
        ("input", "input exceeds"),
        ("line", "line exceeds"),
        ("records", "record count exceeds"),
    ],
)
def test_audit_limits_fail_safely_without_replacing_report(
    tmp_path: Path,
    limit_name: str,
    expected_message: str,
) -> None:
    input_path = tmp_path / "records.jsonl"
    input_path.write_bytes(SMOKE_FIXTURE.read_bytes())
    report_path = tmp_path / "report.json"
    report_path.write_text("sentinel-report", encoding="utf-8")
    if limit_name == "input":
        limits = checker_module.AuditLimits(
            max_input_bytes=8,
            max_line_bytes=256 * 1024,
            max_records=100,
        )
    elif limit_name == "line":
        limits = checker_module.AuditLimits(
            max_input_bytes=1024 * 1024,
            max_line_bytes=32,
            max_records=100,
        )
    else:
        limits = checker_module.AuditLimits(
            max_input_bytes=1024 * 1024,
            max_line_bytes=256 * 1024,
            max_records=1,
        )

    with pytest.raises(ValueError, match=expected_message):
        check_legacy_embeddings(input_path, report_path, limits=limits)
    assert report_path.read_text(encoding="utf-8") == "sentinel-report"


@pytest.mark.parametrize(
    ("record_text", "expected_reason"),
    [
        ("{not-json}\n", "MALFORMED_JSON"),
        (
            json.dumps(
                {
                    "id": "bad-numeric",
                    "embedding": [0.0, "not-a-number"],
                    "feature_dim": 2,
                    "model_sha256": CURRENT_RECOGNIZER_SHA256,
                    "provenance": "legacy-export",
                }
            )
            + "\n",
            "NON_NUMERIC_EMBEDDING",
        ),
        (
            '{"id":"nan","embedding":[NaN],"feature_dim":1,'
            f'"model_sha256":"{CURRENT_RECOGNIZER_SHA256}",'
            '"provenance":"legacy-export"}\n',
            "NON_FINITE_EMBEDDING",
        ),
        (
            '{"id":"infinity","embedding":[Infinity],"feature_dim":1,'
            f'"model_sha256":"{CURRENT_RECOGNIZER_SHA256}",'
            '"provenance":"legacy-export"}\n',
            "NON_FINITE_EMBEDDING",
        ),
    ],
)
def test_malformed_and_unsafe_vectors_are_invalid(
    tmp_path: Path,
    record_text: str,
    expected_reason: str,
) -> None:
    input_path = tmp_path / "records.jsonl"
    input_path.write_text(record_text, encoding="utf-8")

    report = check_legacy_embeddings(input_path, tmp_path / "report.json")
    item = report["groups"]["invalid"]["records"][0]
    assert expected_reason in item["reasons"]


def test_length_alone_never_marks_an_uncertain_record_compatible(
    tmp_path: Path,
) -> None:
    normalized = [1.0 / math.sqrt(CURRENT_FEATURE_DIMENSION)] * CURRENT_FEATURE_DIMENSION
    input_path = tmp_path / "records.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "id": "uncertain",
                "embedding": normalized,
                "feature_dim": CURRENT_FEATURE_DIMENSION,
                "model_sha256": CURRENT_RECOGNIZER_SHA256,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = check_legacy_embeddings(input_path, tmp_path / "report.json")
    assert report["summary"]["compatible"] == 0
    assert report["summary"]["requires_reextract"] == 1
    reasons = report["groups"]["requires_reextract"]["records"][0]["reasons"]
    assert reasons == ["PROVENANCE_UNCERTAIN"]


@pytest.mark.parametrize(
    "provenance",
    [
        "unknown",
        "verified-different-recognizer-model",
        "legacy-export",
    ],
)
def test_only_allowlisted_provenance_is_treated_as_clear(
    tmp_path: Path,
    provenance: str,
) -> None:
    normalized = [1.0 / math.sqrt(CURRENT_FEATURE_DIMENSION)] * CURRENT_FEATURE_DIMENSION
    input_path = tmp_path / "records.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "id": "placeholder-provenance",
                "embedding": normalized,
                "feature_dim": CURRENT_FEATURE_DIMENSION,
                "model_sha256": CURRENT_RECOGNIZER_SHA256,
                "provenance": provenance,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = check_legacy_embeddings(input_path, tmp_path / "report.json")
    assert report["summary"]["compatible"] == 0
    reasons = report["groups"]["requires_reextract"]["records"][0]["reasons"]
    assert reasons == ["PROVENANCE_UNCERTAIN"]


def test_fixture_generator_reads_the_selected_compatibility_manifest(
    tmp_path: Path,
) -> None:
    custom_provenance = "fixture-only-provenance-v1"
    compatibility_path = _write_compatibility_pair(
        tmp_path,
        accepted_provenance=custom_provenance,
    )

    records = build_records(compatibility_path)

    assert len(records[0]["embedding"]) == CURRENT_FEATURE_DIMENSION
    assert records[0]["feature_dim"] == CURRENT_FEATURE_DIMENSION
    assert records[0]["model_sha256"] == CURRENT_RECOGNIZER_SHA256
    assert records[0]["provenance"] == custom_provenance


def test_compatibility_manifest_drift_preserves_existing_report(
    tmp_path: Path,
) -> None:
    compatibility_path = _write_compatibility_pair(tmp_path)
    payload = json.loads(compatibility_path.read_text(encoding="utf-8"))
    payload["recognizer"]["sha256"] = "f" * 64
    compatibility_path.write_text(json.dumps(payload), encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text("sentinel-report", encoding="utf-8")

    with pytest.raises(ValueError, match="recognizer SHA"):
        check_legacy_embeddings(
            SMOKE_FIXTURE,
            report_path,
            compatibility_manifest=compatibility_path,
        )
    assert report_path.read_text(encoding="utf-8") == "sentinel-report"


def test_cli_completes_with_invalid_records_and_emits_report(tmp_path: Path) -> None:
    report_path = tmp_path / "cli-report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "check_legacy_embeddings.py"),
            "--input",
            str(SMOKE_FIXTURE),
            "--report",
            str(report_path),
            "--compatibility-manifest",
            str(COMPATIBILITY_MANIFEST),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(report_path.read_text(encoding="utf-8"))["summary"]["total"] == 3


def test_checker_refuses_to_overwrite_its_input(tmp_path: Path) -> None:
    input_path = tmp_path / "records.jsonl"
    input_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be different"):
        check_legacy_embeddings(input_path, input_path)


def test_empty_input_is_an_overall_format_failure(tmp_path: Path) -> None:
    input_path = tmp_path / "records.jsonl"
    input_path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no JSONL records"):
        check_legacy_embeddings(input_path, tmp_path / "report.json")
