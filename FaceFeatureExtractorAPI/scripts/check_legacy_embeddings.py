"""Bounded, streaming compatibility audit for exported legacy embeddings."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from face_algorithm.runtime.legacy_compatibility import (
    DEFAULT_COMPATIBILITY_MANIFEST,
    LegacyCompatibility,
    load_legacy_compatibility,
)

_GROUP_NAMES = ("compatible", "requires_reextract", "invalid")


@dataclass(frozen=True, slots=True)
class AuditLimits:
    max_input_bytes: int
    max_line_bytes: int
    max_records: int

    def __post_init__(self) -> None:
        for name, value in (
            ("max_input_bytes", self.max_input_bytes),
            ("max_line_bytes", self.max_line_bytes),
            ("max_records", self.max_records),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_AUDIT_LIMITS = AuditLimits(
    max_input_bytes=512 * 1024 * 1024,
    max_line_bytes=256 * 1024,
    max_records=100_000,
)


@dataclass(frozen=True, slots=True)
class _FileSignature:
    size: int
    modified_ns: int

    @classmethod
    def from_stat(cls, stat_result: os.stat_result) -> _FileSignature:
        return cls(size=stat_result.st_size, modified_ns=stat_result.st_mtime_ns)


class _DuplicateJsonKey(ValueError):
    pass


class _NonFiniteJsonConstant(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise _NonFiniteJsonConstant(value)


def _record_result(
    record_id: str,
    reasons: list[str],
    *,
    can_reextract: bool | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"id": record_id, "reasons": reasons}
    if can_reextract is not None:
        result["can_reextract"] = can_reextract
    return result


def _classify_line(
    line: str,
    line_number: int,
    compatibility: LegacyCompatibility,
) -> tuple[str, dict[str, Any], str | None]:
    fallback_id = f"line-{line_number}"
    try:
        payload = json.loads(
            line,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except _DuplicateJsonKey:
        return (
            "invalid",
            _record_result(fallback_id, ["DUPLICATE_JSON_KEY"]),
            None,
        )
    except _NonFiniteJsonConstant:
        return (
            "invalid",
            _record_result(fallback_id, ["NON_FINITE_EMBEDDING"]),
            None,
        )
    except (json.JSONDecodeError, ValueError):
        return "invalid", _record_result(fallback_id, ["MALFORMED_JSON"]), None

    if not isinstance(payload, dict):
        return "invalid", _record_result(fallback_id, ["INVALID_RECORD"]), None

    raw_id = payload.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return (
            "invalid",
            _record_result(fallback_id, ["MISSING_STABLE_ID"]),
            None,
        )
    record_id = raw_id.strip()

    embedding = payload.get("embedding")
    if not isinstance(embedding, list):
        return (
            "invalid",
            _record_result(record_id, ["INVALID_EMBEDDING_TYPE"]),
            record_id,
        )
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in embedding
    ):
        return (
            "invalid",
            _record_result(record_id, ["NON_NUMERIC_EMBEDDING"]),
            record_id,
        )
    try:
        finite = all(math.isfinite(value) for value in embedding)
    except OverflowError:
        finite = False
    if not finite:
        return (
            "invalid",
            _record_result(record_id, ["NON_FINITE_EMBEDDING"]),
            record_id,
        )

    declared_dimension = payload.get("feature_dim")
    if declared_dimension is not None and (
        isinstance(declared_dimension, bool)
        or not isinstance(declared_dimension, int)
        or declared_dimension <= 0
    ):
        return (
            "invalid",
            _record_result(record_id, ["INVALID_FEATURE_DIMENSION"]),
            record_id,
        )
    if declared_dimension is not None and declared_dimension != len(embedding):
        return (
            "invalid",
            _record_result(record_id, ["DIMENSION_CONFLICT"]),
            record_id,
        )

    reasons: list[str] = []
    if declared_dimension is None:
        reasons.append("FEATURE_DIMENSION_UNCERTAIN")
    elif declared_dimension != compatibility.feature_dimension:
        reasons.append("FEATURE_DIMENSION_MISMATCH")
    if (
        len(embedding) != compatibility.feature_dimension
        and "FEATURE_DIMENSION_MISMATCH" not in reasons
    ):
        reasons.append("FEATURE_DIMENSION_MISMATCH")

    model_sha = payload.get("model_sha256")
    if not isinstance(model_sha, str) or len(model_sha) != 64:
        reasons.append("MODEL_SHA_UNCERTAIN")
    elif model_sha != compatibility.recognizer.sha256:
        reasons.append("MODEL_SHA_MISMATCH")

    if payload.get("provenance") not in compatibility.accepted_provenance:
        reasons.append("PROVENANCE_UNCERTAIN")

    if reasons:
        source_path = payload.get("source_photo_path")
        can_reextract = isinstance(source_path, str) and bool(source_path.strip())
        return (
            "requires_reextract",
            _record_result(record_id, reasons, can_reextract=can_reextract),
            record_id,
        )
    return "compatible", _record_result(record_id, []), record_id


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _stream_audit(
    source: Path,
    compatibility: LegacyCompatibility,
    limits: AuditLimits,
) -> tuple[dict[str, list[dict[str, Any]]], int, str]:
    initial_signature = _FileSignature.from_stat(source.stat())
    if initial_signature.size > limits.max_input_bytes:
        raise ValueError("input exceeds max_input_bytes")

    grouped: dict[str, list[dict[str, Any]]] = {
        name: [] for name in _GROUP_NAMES
    }
    seen_ids: set[str] = set()
    digest = hashlib.sha256()
    bytes_read = 0
    record_count = 0

    with source.open("rb") as stream:
        opened_signature = _FileSignature.from_stat(os.fstat(stream.fileno()))
        if opened_signature != initial_signature:
            raise ValueError("input changed during audit")
        line_number = 0
        while True:
            raw_line = stream.readline(limits.max_line_bytes + 1)
            if not raw_line:
                break
            line_number += 1
            if len(raw_line) > limits.max_line_bytes:
                raise ValueError("line exceeds max_line_bytes")
            bytes_read += len(raw_line)
            if bytes_read > limits.max_input_bytes:
                raise ValueError("input exceeds max_input_bytes")
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            record_count += 1
            if record_count > limits.max_records:
                raise ValueError("record count exceeds max_records")
            line = raw_line.decode("utf-8")
            group, record, stable_id = _classify_line(
                line,
                line_number,
                compatibility,
            )
            if stable_id is not None:
                if stable_id in seen_ids:
                    raise ValueError(f"duplicate record id: {stable_id}")
                seen_ids.add(stable_id)
            grouped[group].append(record)
        ending_signature = _FileSignature.from_stat(os.fstat(stream.fileno()))

    final_signature = _FileSignature.from_stat(source.stat())
    if (
        ending_signature != initial_signature
        or final_signature != initial_signature
        or bytes_read != initial_signature.size
    ):
        raise ValueError("input changed during audit")
    if record_count == 0:
        raise ValueError("input contains no JSONL records")
    return grouped, record_count, digest.hexdigest()


def check_legacy_embeddings(
    input_path: str | Path,
    report_path: str | Path,
    *,
    compatibility_manifest: str | Path = DEFAULT_COMPATIBILITY_MANIFEST,
    limits: AuditLimits = DEFAULT_AUDIT_LIMITS,
) -> dict[str, Any]:
    """Audit JSONL records without mutating input or retaining vector values."""

    source = Path(input_path).expanduser().resolve()
    destination = Path(report_path).expanduser().resolve()
    if source == destination:
        raise ValueError("input and report paths must be different")
    compatibility = load_legacy_compatibility(compatibility_manifest)
    grouped, total, input_sha256 = _stream_audit(source, compatibility, limits)

    report: dict[str, Any] = {
        "input": {"sha256": input_sha256, "record_count": total},
        "current_contract": {
            "manifest_version": compatibility.manifest_version,
            "feature_version": compatibility.feature_version.value,
            "feature_dimension": compatibility.feature_dimension,
            "recognizer_name": compatibility.recognizer.name,
            "recognizer_sha256": compatibility.recognizer.sha256,
            "accepted_provenance": list(compatibility.accepted_provenance),
            "models_manifest": str(compatibility.models_manifest),
        },
        "summary": {
            "compatible": len(grouped["compatible"]),
            "requires_reextract": len(grouped["requires_reextract"]),
            "invalid": len(grouped["invalid"]),
            "total": total,
        },
        "groups": {
            name: {"count": len(grouped[name]), "records": grouped[name]}
            for name in _GROUP_NAMES
        },
    }
    _atomic_write_json(destination, report)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit exported legacy embeddings without modifying them."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--compatibility-manifest",
        type=Path,
        default=DEFAULT_COMPATIBILITY_MANIFEST,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = check_legacy_embeddings(
            args.input,
            args.report,
            compatibility_manifest=args.compatibility_manifest,
        )
    except (OSError, UnicodeError, ValueError) as error:
        print(f"legacy embedding check failed: {error}")
        return 2
    summary = report["summary"]
    print(
        "legacy embedding check complete: "
        f"compatible={summary['compatible']} "
        f"requires_reextract={summary['requires_reextract']} "
        f"invalid={summary['invalid']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
