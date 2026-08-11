"""Explicitly install the pinned MODNet checkpoint after integrity checks."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import BinaryIO, Final, Sequence


CHECKPOINT_NAME: Final = "modnet_photographic_portrait_matting.ckpt"
CHECKPOINT_SIZE: Final = 26_255_603
CHECKPOINT_SHA256: Final = (
    "7c22235f0925deba15d4d63e53afcb654c47055bbcd98f56e393ab2584007ed8"
)
CHECKPOINT_URL: Final = (
    "https://drive.google.com/uc?export=download&id="
    "1mcr7ALciuAsHCpLnrtG_eop5-EYhbCmz"
)
_COPY_CHUNK_BYTES: Final = 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS: Final = 60.0


class CheckpointPreparationError(RuntimeError):
    """The candidate artifact did not match the pinned checkpoint."""


def _default_output() -> Path:
    return Path(__file__).resolve().parents[1] / "checkpoints" / CHECKPOINT_NAME


def _install_stream(stream: BinaryIO, destination: Path) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".part",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            digest = hashlib.sha256()
            actual_size = 0
            while True:
                chunk = stream.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise TypeError("checkpoint source must yield bytes")
                actual_size += len(chunk)
                if actual_size > CHECKPOINT_SIZE:
                    raise CheckpointPreparationError(
                        "checkpoint exceeds the pinned size"
                    )
                digest.update(chunk)
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())

        actual_sha256 = digest.hexdigest()
        if actual_size != CHECKPOINT_SIZE or actual_sha256 != CHECKPOINT_SHA256:
            raise CheckpointPreparationError(
                "checkpoint size or SHA-256 does not match the pinned artifact"
            )
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install the pinned MODNet checkpoint. No network access occurs "
            "unless --download is explicitly selected."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--source-file",
        type=Path,
        help="copy and verify an existing local checkpoint",
    )
    source.add_argument(
        "--download",
        action="store_true",
        help="download from the pinned upstream URL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_default_output(),
        help="destination path (defaults to the project checkpoints directory)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.source_file is not None:
            with args.source_file.expanduser().resolve().open("rb") as source:
                _install_stream(source, args.output)
        else:
            with urllib.request.urlopen(
                CHECKPOINT_URL,
                timeout=_DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                _install_stream(response, args.output)
    except (CheckpointPreparationError, OSError, TypeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
