from __future__ import annotations

import json
import shutil
import subprocess
import sys
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {' '.join(command)}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


@pytest.mark.integration
def test_built_wheel_loads_default_config_and_models_outside_source_tree(
    tmp_path: Path,
) -> None:
    build_source = tmp_path / "source"
    shutil.copytree(
        PROJECT_ROOT,
        build_source,
        ignore=shutil.ignore_patterns(
            ".venv",
            ".pytest_cache",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
            "build",
            "dist",
        ),
    )
    wheel_directory = tmp_path / "wheel"
    wheel_directory.mkdir()
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_directory),
        ],
        cwd=build_source,
    )
    wheels = list(wheel_directory.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        assert len(metadata_names) == 1
        metadata = Parser().parsestr(
            archive.read(metadata_names[0]).decode("utf-8")
        )
    requires_dist = set(metadata.get_all("Requires-Dist", []))
    assert "cryptography==46.0.3" in requires_dist
    assert "fastapi==0.137.1" in requires_dist
    assert "numpy==2.5.1" in requires_dist
    assert "opencv-python-headless==5.0.0.93" in requires_dist
    assert "onnxruntime==1.28.0" in requires_dist
    assert "pydantic==2.12.4" in requires_dist
    assert "python-multipart==0.0.20" in requires_dist
    assert "PyYAML==6.0.3" in requires_dist
    assert "uvicorn==0.52.1" in requires_dist
    assert metadata.get_all("Provides-Extra", []) == ["portrait"]
    assert 'torch==2.13.0; extra == "portrait"' in requires_dist
    assert "face_algorithm/runtime/config.py" in names
    assert "face_feature_extractor.py" in names
    assert "feature_server.py" in names
    assert "api/app.py" in names
    assert "api/media.py" in names
    assert "api/security.py" in names
    assert any(name.startswith("models/") and name.endswith(".py") for name in names)
    assert not any(name.startswith("tests/") for name in names)
    assert not any("synthetic_person_holding_blank_card" in name for name in names)
    assert any(
        name.endswith("face_algorithm/portrait/_modnet_arch/modnet.py")
        for name in names
    )
    assert any(
        name.endswith(
            "face_algorithm/portrait/_modnet_arch/backbones/mobilenetv2.py"
        )
        for name in names
    )
    assert any(name.endswith("face_algorithm/video/verifier.py") for name in names)
    assert any(name.endswith("face_algorithm/video/decoder.py") for name in names)
    assert sum(name.endswith(".data/data/THIRD_PARTY_NOTICES.md") for name in names) == 1
    assert sum(
        name.endswith(".data/data/third_party/MODNet/LICENSE") for name in names
    ) == 1
    assert sum(
        name.endswith(".data/data/scripts/prepare_modnet.py") for name in names
    ) == 1
    assert sum(
        name.endswith(".data/data/scripts/generate_fixture_video.py")
        for name in names
    ) == 1
    assert not any(name.casefold().endswith(".ckpt") for name in names)
    assert sum(name.endswith(".data/data/config/algorithm.yaml") for name in names) == 1
    assert sum(
        ".data/data/checkpoints/" in name and name.endswith(".onnx") for name in names
    ) == 7
    assert sum(name.endswith(".data/data/checkpoints/README.md") for name in names) == 1
    assert sum(name.endswith(".data/data/models.manifest.json") for name in names) == 1
    assert sum(
        name.endswith(".data/data/legacy-compatibility.manifest.json")
        for name in names
    ) == 1

    target = tmp_path / "site"
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-compile",
            "--target",
            str(target),
            str(wheel),
        ],
        cwd=tmp_path,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    probe = r"""
import json
import os
import sys
from pathlib import Path

target = Path(sys.argv[1]).resolve()
outside = Path(sys.argv[2]).resolve()
source = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(target))
os.chdir(outside)

import face_algorithm
from face_algorithm.runtime.config import load_config, resolve_config_path
from face_algorithm.runtime.legacy_compatibility import load_legacy_compatibility
from face_algorithm.verification import (
    ImageVerificationRuntime,
    build_image_verification_runtime,
)
from face_algorithm.portrait import (
    PortraitMattingRuntime,
    build_portrait_matting_runtime,
)
from face_algorithm.video import (
    VideoVerificationRuntime,
    build_video_verification_runtime,
)
from face_algorithm.witness import WitnessBlurRuntime, build_witness_blur_runtime
from api.app import create_default_app

module_path = Path(face_algorithm.__file__).resolve()
assert module_path.is_relative_to(target), module_path
assert all(Path(item).resolve() != source for item in sys.path if item)
config_path = resolve_config_path()
config = load_config()
compatibility = load_legacy_compatibility()
models = config.models
entries = [
    getattr(models, name)
    for name in type(models).model_fields
    if name != "intra_op_num_threads"
]
enabled = [entry for entry in entries if entry.enabled]
assert config_path == target / "config" / "algorithm.yaml", config_path
assert len(entries) == 8
assert len(enabled) == 7
assert all(entry.path.is_absolute() for entry in entries)
assert all(entry.path.is_file() for entry in enabled)
assert config.models.face_recognizer.expected_output_dimension == 1024
assert compatibility.models_manifest == target / "models.manifest.json"
assert compatibility.feature_dimension == 1024
assert compatibility.recognizer.sha256 == config.models.face_recognizer.sha256
assert ImageVerificationRuntime.__module__.startswith("face_algorithm.verification")
assert callable(build_image_verification_runtime)
assert PortraitMattingRuntime.__module__.startswith("face_algorithm.portrait")
assert callable(build_portrait_matting_runtime)
assert VideoVerificationRuntime.__module__.startswith("face_algorithm.video")
assert callable(build_video_verification_runtime)
assert WitnessBlurRuntime.__module__.startswith("face_algorithm.witness")
assert callable(build_witness_blur_runtime)
app = create_default_app()
assert app.title == "Private Face Algorithm API"
assert len(list(target.rglob("algorithm.yaml"))) == 1
assert not (target / "tests").exists()
print(json.dumps({
    "config_path": str(config_path),
    "module_path": str(module_path),
    "enabled_models": [str(entry.path) for entry in enabled],
    "compatibility_manifest": str(target / "legacy-compatibility.manifest.json"),
}))
"""
    result = _run(
        [
            sys.executable,
            "-I",
            "-c",
            probe,
            str(target),
            str(outside),
            str(PROJECT_ROOT),
        ],
        cwd=outside,
    )
    details = json.loads(result.stdout)
    details["wheel_bytes"] = wheel.stat().st_size
    print(json.dumps(details, ensure_ascii=False, sort_keys=True))
    assert Path(details["config_path"]).is_file()
    assert Path(details["module_path"]).is_relative_to(target)
    assert len(details["enabled_models"]) == 7
    assert Path(details["compatibility_manifest"]).is_file()
    assert details["wheel_bytes"] > 100_000_000

    missing_extra_probe = r"""
import importlib.abc
import os
import sys
from pathlib import Path

target = Path(sys.argv[1]).resolve()
outside = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(target))
os.chdir(outside)

class BlockTorch(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "torch" or fullname.startswith("torch."):
            raise ModuleNotFoundError(
                "torch is intentionally absent",
                name=fullname,
            )
        return None

sys.meta_path.insert(0, BlockTorch())

from face_algorithm.portrait import (
    PortraitMattingDependencyError,
    PortraitMattingRuntime,
    build_portrait_matting_runtime,
)
from face_algorithm.runtime.config import load_config

config = load_config()
models = config.models.model_copy(update={
    "portrait_matting": config.models.portrait_matting.model_copy(
        update={"enabled": True}
    )
})
config = config.model_copy(update={"models": models})

class UntouchedRegistry:
    calls = 0

    def get_with_evidence(self, name):
        self.calls += 1
        raise AssertionError("registry must not be touched without the extra")

registry = UntouchedRegistry()
try:
    build_portrait_matting_runtime(registry=registry, config=config)
except PortraitMattingDependencyError as error:
    assert "face-algorithm[portrait]" in str(error)
else:
    raise AssertionError("missing portrait extra must fail explicitly")

assert PortraitMattingRuntime.__module__.startswith("face_algorithm.portrait")
assert registry.calls == 0
assert "torch" not in sys.modules
"""
    _run(
        [
            sys.executable,
            "-I",
            "-c",
            missing_extra_probe,
            str(target),
            str(outside),
        ],
        cwd=outside,
    )
