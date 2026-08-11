"""Verify the model manifest without loading model tensors or sessions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from face_algorithm.runtime.model_registry import verify_manifest_artifacts
from face_algorithm.schemas import HealthLevel


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify local model artifacts.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=PROJECT_ROOT / "models.manifest.json",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit machine-readable health JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        health = verify_manifest_artifacts(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"manifest verification failed: {error}", file=sys.stderr)
        return 2

    if args.as_json:
        print(health.model_dump_json(indent=2))
    else:
        print(f"manifest health: {health.status.value}")
        for model in health.models:
            requirement = "required" if model.required else "optional"
            detail = f" error={model.error_code.value}" if model.error_code else ""
            print(f"{model.name}: {model.state.value} ({requirement}){detail}")
    return 1 if health.status is HealthLevel.UNHEALTHY else 0


if __name__ == "__main__":
    raise SystemExit(main())
