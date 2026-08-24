"""Deterministic, privacy-safe smoke test for anonymous field images."""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np


_CAPABILITIES = {
    "portrait": "/api/v2/portrait/generate",
    "witness": "/api/v2/witness/blur-background",
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _image_metadata(path: Path) -> tuple[int | None, int | None]:
    encoded = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        return None, None
    return int(image.shape[1]), int(image.shape[0])


def _multipart_image(payload: bytes, suffix: str) -> tuple[bytes, str]:
    boundary = f"----face-algorithm-smoke-{uuid.uuid4().hex}"
    is_png = suffix.casefold() == ".png"
    filename = "sample.png" if is_png else "sample.jpg"
    media_type = "image/png" if is_png else "image/jpeg"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {media_type}\r\n\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    return prefix + payload + suffix, f"multipart/form-data; boundary={boundary}"


def _request(
    base_url: str,
    capability: str,
    payload: bytes,
    suffix: str,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any], float]:
    body, content_type = _multipart_image(payload, suffix)
    request = urllib.request.Request(
        base_url.rstrip("/") + _CAPABILITIES[capability],
        data=body,
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as error:
        status = int(error.code)
        raw = error.read()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = {"error": {"code": "NON_JSON_RESPONSE"}}
    if not isinstance(decoded, dict):
        decoded = {"error": {"code": "INVALID_JSON_ROOT"}}
    decoded.pop("image_base64", None)
    return status, decoded, elapsed_ms


def _reason_codes(payload: dict[str, Any]) -> list[str]:
    values = payload.get("reason_codes", [])
    if not isinstance(values, list):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return [error["code"]]
        return ["INVALID_REASON_CODES"]
    return [str(value) for value in values]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a fixed-seed anonymous portrait/witness field smoke test."
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=2_026_001)
    parser.add_argument(
        "--capabilities",
        nargs="+",
        choices=tuple(_CAPABILITIES),
        default=tuple(_CAPABILITIES),
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.sample_size <= 0:
        raise SystemExit("sample-size must be positive")
    if args.timeout_seconds <= 0:
        raise SystemExit("timeout-seconds must be positive")
    files = sorted(
        path
        for path in args.input_dir.rglob("*")
        if path.is_file() and path.suffix.casefold() in _IMAGE_SUFFIXES
    )
    if len(files) < args.sample_size:
        raise SystemExit(
            f"input directory has {len(files)} supported images, "
            f"below requested sample size {args.sample_size}"
        )
    selected = random.Random(args.seed).sample(files, args.sample_size)
    results: list[dict[str, Any]] = []
    total = len(selected) * len(args.capabilities)
    completed = 0
    for index, path in enumerate(selected, start=1):
        payload = path.read_bytes()
        width, height = _image_metadata(path)
        for capability in args.capabilities:
            completed += 1
            anonymous_id = f"sample-{index:03d}"
            try:
                status, response, elapsed_ms = _request(
                    args.base_url,
                    capability,
                    payload,
                    path.suffix,
                    args.timeout_seconds,
                )
                reasons = _reason_codes(response)
                record = {
                    "sample_id": anonymous_id,
                    "capability": capability,
                    "width": width,
                    "height": height,
                    "pixels": None if width is None or height is None else width * height,
                    "input_bytes": len(payload),
                    "http_status": status,
                    "success": response.get("success") is True,
                    "suggestion": response.get("suggestion"),
                    "reason_codes": reasons,
                    "card_region_count": len(response.get("card_regions", []))
                    if isinstance(response.get("card_regions"), list)
                    else 0,
                    "elapsed_ms": round(elapsed_ms, 1),
                }
            except Exception as error:  # noqa: BLE001 - smoke runner must continue.
                record = {
                    "sample_id": anonymous_id,
                    "capability": capability,
                    "width": width,
                    "height": height,
                    "pixels": None if width is None or height is None else width * height,
                    "input_bytes": len(payload),
                    "http_status": None,
                    "success": False,
                    "suggestion": None,
                    "reason_codes": [type(error).__name__],
                    "card_region_count": 0,
                    "elapsed_ms": None,
                }
            results.append(record)
            reasons_text = ",".join(record["reason_codes"]) or "NONE"
            print(
                f"[{completed:02d}/{total:02d}] {anonymous_id} {capability}: "
                f"HTTP={record['http_status']} suggestion={record['suggestion']} "
                f"reasons={reasons_text}",
                flush=True,
            )

    summary: dict[str, Any] = {
        "base_url": args.base_url,
        "seed": args.seed,
        "sample_size": args.sample_size,
        "capabilities": {},
    }
    for capability in args.capabilities:
        capability_results = [
            item for item in results if item["capability"] == capability
        ]
        elapsed_values = [
            float(item["elapsed_ms"])
            for item in capability_results
            if item["elapsed_ms"] is not None
        ]
        summary["capabilities"][capability] = {
            "success_count": sum(item["success"] for item in capability_results),
            "suggestions": dict(
                Counter(str(item["suggestion"]) for item in capability_results)
            ),
            "reason_codes": dict(
                Counter(
                    reason
                    for item in capability_results
                    for reason in item["reason_codes"]
                )
            ),
            "mean_elapsed_ms": round(
                sum(elapsed_values) / len(elapsed_values),
                1,
            ) if elapsed_values else None,
        }
    report = {"summary": summary, "results": results}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    if args.output_json is not None:
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
