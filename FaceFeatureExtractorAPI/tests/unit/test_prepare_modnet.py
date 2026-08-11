from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import scripts.prepare_modnet as prepare_modnet


def _configure_small_artifact(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> None:
    monkeypatch.setattr(prepare_modnet, "CHECKPOINT_SIZE", len(payload))
    monkeypatch.setattr(
        prepare_modnet,
        "CHECKPOINT_SHA256",
        hashlib.sha256(payload).hexdigest(),
    )


def test_official_checkpoint_identity_is_pinned() -> None:
    assert prepare_modnet.CHECKPOINT_NAME == (
        "modnet_photographic_portrait_matting.ckpt"
    )
    assert prepare_modnet.CHECKPOINT_SIZE == 26_255_603
    assert prepare_modnet.CHECKPOINT_SHA256 == (
        "7c22235f0925deba15d4d63e53afcb654c47055bbcd98f56e393ab2584007ed8"
    )
    assert prepare_modnet.CHECKPOINT_URL == (
        "https://drive.google.com/uc?export=download&id="
        "1mcr7ALciuAsHCpLnrtG_eop5-EYhbCmz"
    )


def test_source_file_is_verified_and_atomically_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"verified-modnet" * 1000
    _configure_small_artifact(monkeypatch, payload)
    source = tmp_path / "source.ckpt"
    destination = tmp_path / "checkpoints" / "model.ckpt"
    source.write_bytes(payload)
    replace_calls: list[tuple[Path, Path]] = []
    fsync_calls: list[int] = []
    original_replace = os.replace
    original_fsync = os.fsync

    def recording_replace(source_path, destination_path):
        replace_calls.append((Path(source_path), Path(destination_path)))
        return original_replace(source_path, destination_path)

    def recording_fsync(fd):
        fsync_calls.append(fd)
        return original_fsync(fd)

    monkeypatch.setattr(prepare_modnet.os, "replace", recording_replace)
    monkeypatch.setattr(prepare_modnet.os, "fsync", recording_fsync)

    result = prepare_modnet.main(
        ["--source-file", str(source), "--output", str(destination)]
    )

    assert result == 0
    assert destination.read_bytes() == payload
    assert len(replace_calls) == 1
    staged, replaced_destination = replace_calls[0]
    assert staged.parent == destination.parent
    assert staged.name.startswith(f".{destination.name}.")
    assert staged.name.endswith(".part")
    assert replaced_destination == destination
    assert fsync_calls
    assert list(destination.parent.glob("*.part")) == []


def test_invalid_source_never_overwrites_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = b"correct"
    _configure_small_artifact(monkeypatch, expected)
    source = tmp_path / "wrong.ckpt"
    destination = tmp_path / "model.ckpt"
    source.write_bytes(b"wrong")
    destination.write_bytes(b"keep-me")

    with pytest.raises(SystemExit):
        prepare_modnet.main(
            ["--source-file", str(source), "--output", str(destination)]
        )

    assert destination.read_bytes() == b"keep-me"
    assert list(tmp_path.glob("*.part")) == []


def test_download_occurs_only_when_explicitly_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"downloaded"
    _configure_small_artifact(monkeypatch, payload)
    calls: list[tuple[str, float]] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            if not hasattr(self, "remaining"):
                self.remaining = payload
            chunk, self.remaining = self.remaining[:size], self.remaining[size:]
            return chunk

    def fake_urlopen(url, *, timeout):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr(prepare_modnet.urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / "download.ckpt"

    result = prepare_modnet.main(["--download", "--output", str(destination)])

    assert result == 0
    assert destination.read_bytes() == payload
    assert calls == [(prepare_modnet.CHECKPOINT_URL, 60.0)]


def test_no_arguments_never_attempts_network(monkeypatch) -> None:
    monkeypatch.setattr(
        prepare_modnet.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("network must not be used"),
    )

    with pytest.raises(SystemExit):
        prepare_modnet.main([])


def test_source_file_and_download_are_mutually_exclusive(tmp_path: Path) -> None:
    source = tmp_path / "source.ckpt"
    source.write_bytes(b"anything")

    with pytest.raises(SystemExit):
        prepare_modnet.main(["--source-file", str(source), "--download"])
