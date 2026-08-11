"""Generate deterministic, non-biometric legacy-vector smoke fixtures."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from face_algorithm.runtime.legacy_compatibility import (
    DEFAULT_COMPATIBILITY_MANIFEST,
    load_legacy_compatibility,
)


def build_records(
    compatibility_manifest: str | Path = DEFAULT_COMPATIBILITY_MANIFEST,
) -> list[dict]:
    compatibility = load_legacy_compatibility(compatibility_manifest)
    dimension = compatibility.feature_dimension
    normalized = [1.0 / math.sqrt(dimension)] * dimension
    accepted_provenance = compatibility.accepted_provenance[0]
    return [
        {
            "id": "compatible-001",
            "embedding": normalized,
            "feature_dim": dimension,
            "model_sha256": compatibility.recognizer.sha256,
            "provenance": accepted_provenance,
        },
        {
            "id": "reextract-001",
            "embedding": normalized,
            "feature_dim": dimension,
            "model_sha256": "unknown",
            "provenance": "legacy-export-without-model-hash",
            "source_photo_path": "archive/reextract-001.jpg",
        },
        {
            "id": "invalid-001",
            "embedding": normalized[:-1],
            "feature_dim": dimension,
            "model_sha256": compatibility.recognizer.sha256,
            "provenance": accepted_provenance,
        },
    ]


def main() -> None:
    output = Path(__file__).resolve().parent / "generated" / "legacy_embeddings.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for record in build_records():
            stream.write(json.dumps(record, separators=(",", ":"), allow_nan=False))
            stream.write("\n")


if __name__ == "__main__":
    main()
