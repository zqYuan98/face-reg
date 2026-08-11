from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PINNED_COMMIT = "28165a451e4610c9d77cfdf925a94610bb2810fb"
VENDORED_FILES = (
    PROJECT_ROOT / "face_algorithm" / "portrait" / "_modnet_arch" / "modnet.py",
    PROJECT_ROOT
    / "face_algorithm"
    / "portrait"
    / "_modnet_arch"
    / "backbones"
    / "__init__.py",
    PROJECT_ROOT
    / "face_algorithm"
    / "portrait"
    / "_modnet_arch"
    / "backbones"
    / "wrapper.py",
    PROJECT_ROOT
    / "face_algorithm"
    / "portrait"
    / "_modnet_arch"
    / "backbones"
    / "mobilenetv2.py",
)


def test_each_vendored_modnet_file_records_pinned_source_and_license() -> None:
    for path in VENDORED_FILES:
        source = path.read_text(encoding="utf-8")
        assert PINNED_COMMIT in source, path
        assert "Apache-2.0" in source, path
        assert "inference-only" in source, path


def test_upstream_comments_required_for_provenance_are_preserved() -> None:
    architecture = VENDORED_FILES[0].read_text(encoding="utf-8")
    wrapper = VENDORED_FILES[2].read_text(encoding="utf-8")
    mobilenet = VENDORED_FILES[3].read_text(encoding="utf-8")

    assert "MODNet Basic Modules" in architecture
    assert "MODNet Branches" in architecture
    assert "the pre-trained model is provided by" in wrapper
    assert "Human-Segmentation-PyTorch" in mobilenet


def test_full_upstream_license_and_third_party_notice_are_present() -> None:
    license_text = (
        PROJECT_ROOT / "third_party" / "MODNet" / "LICENSE"
    ).read_text(encoding="utf-8")
    notice = (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "END OF TERMS AND CONDITIONS" in license_text
    assert "https://github.com/ZHKKKe/MODNet" in notice
    assert PINNED_COMMIT in notice
    assert "inference-only" in notice


def test_third_party_notice_is_ascii_safe_for_windows_packaging() -> None:
    notice_bytes = (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_bytes()
    notice = notice_bytes.decode("ascii")

    assert "\ufffd" not in notice
    assert "鈙" not in notice


def test_runtime_package_contains_no_checkpoint_download_code() -> None:
    runtime_sources = (
        PROJECT_ROOT / "face_algorithm"
    ).rglob("*.py")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_sources)

    assert "urlopen(" not in combined
    assert "drive.google.com/uc?export=download" not in combined
