"""Deterministically derive a short test video from the synthetic still fixture."""

from __future__ import annotations

import argparse
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class GeneratedVideoMetadata:
    width: int
    height: int
    fps: float
    duration_seconds: float
    frame_count: int
    sha256: str


def derive_frame(
    source: np.ndarray,
    *,
    frame_index: int,
    total_frames: int,
) -> np.ndarray:
    """Apply a small deterministic affine/brightness perturbation."""

    if (
        not isinstance(source, np.ndarray)
        or source.dtype != np.uint8
        or source.ndim != 3
        or source.shape[2] != 3
        or source.shape[0] <= 0
        or source.shape[1] <= 0
    ):
        raise ValueError("source must be a non-empty uint8 BGR image")
    if (
        isinstance(frame_index, bool)
        or not isinstance(frame_index, int)
        or isinstance(total_frames, bool)
        or not isinstance(total_frames, int)
        or total_frames <= 0
        or not 0 <= frame_index < total_frames
    ):
        raise ValueError("frame index must fall within a positive frame count")
    phase = 2.0 * math.pi * frame_index / total_frames
    shift_x = 3.0 * math.sin(phase)
    shift_y = 2.0 * math.cos(phase)
    matrix = np.asarray([[1.0, 0.0, shift_x], [0.0, 1.0, shift_y]], dtype=np.float32)
    height, width = source.shape[:2]
    shifted = cv2.warpAffine(
        source,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    brightness = int(round(4.0 * math.sin(phase * 2.0)))
    return np.clip(shifted.astype(np.int16) + brightness, 0, 255).astype(np.uint8)


def generate_fixture_video(
    *,
    input_path: Path,
    output_path: Path,
    fps: float = 4.0,
    duration_seconds: float = 10.0,
) -> GeneratedVideoMetadata:
    """Write an MP4V video suitable for real-model quick/full smoke tests."""

    if (
        isinstance(fps, bool)
        or not isinstance(fps, (int, float))
        or not math.isfinite(float(fps))
        or not 0.0 < float(fps) <= 120.0
    ):
        raise ValueError("fps must be finite and in (0, 120]")
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(float(duration_seconds))
        or duration_seconds <= 0.0
    ):
        raise ValueError("duration must be finite and positive")
    input_path = Path(input_path)
    output_path = Path(output_path)
    encoded = np.frombuffer(input_path.read_bytes(), dtype=np.uint8)
    source = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if source is None or source.dtype != np.uint8 or source.ndim != 3:
        raise ValueError("input image could not be decoded")
    frame_count = int(round(float(fps) * float(duration_seconds)))
    if frame_count <= 0:
        raise ValueError("video must contain at least one frame")
    height, width = source.shape[:2]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(fps),
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError("MP4V video writer is unavailable")
    try:
        for frame_index in range(frame_count):
            writer.write(
                derive_frame(
                    source,
                    frame_index=frame_index,
                    total_frames=frame_count,
                )
            )
    finally:
        writer.release()
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("fixture video was not written")
    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return GeneratedVideoMetadata(
        width=width,
        height=height,
        fps=float(fps),
        duration_seconds=float(duration_seconds),
        frame_count=frame_count,
        sha256=digest,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    metadata = generate_fixture_video(
        input_path=args.input,
        output_path=args.output,
        fps=args.fps,
        duration_seconds=args.duration_seconds,
    )
    print(
        f"generated {metadata.frame_count} frames, {metadata.duration_seconds:.3f}s, "
        f"sha256={metadata.sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
