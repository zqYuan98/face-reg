from __future__ import annotations

import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_cpu_dockerfile_matches_python_contract_and_runs_non_root_single_worker() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12" in dockerfile
    assert "requirements-cpu.txt" in dockerfile
    assert "USER facealgo" in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert '"--no-proxy-headers"' in dockerfile
    assert "python:3.10" not in dockerfile
    assert "onnxruntime-gpu" not in dockerfile
    assert "allow_origins=[\"*\"]" not in dockerfile


def test_cpu_requirements_are_exact_and_never_mix_gpu_onnxruntime() -> None:
    requirements = (PROJECT_ROOT / "requirements-cpu.txt").read_text(encoding="utf-8")
    live_lines = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and not line.startswith("--")
    ]

    assert "onnxruntime==1.28.0" in live_lines
    assert "torch==2.13.0+cpu" in live_lines
    assert "onnxruntime-gpu" not in requirements
    assert all("~=" not in line and ">=" not in line and "<=" not in line for line in live_lines)
    assert (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").strip() == "-r requirements-cpu.txt"


def test_wheel_metadata_declares_api_runtime_dependencies() -> None:
    payload = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload["project"]

    assert project["requires-python"] == ">=3.12,<3.13"
    dependencies = set(project["dependencies"])
    assert "fastapi==0.137.1" in dependencies
    assert "uvicorn==0.52.1" in dependencies
    assert "python-multipart==0.0.20" in dependencies


def test_docker_context_excludes_secrets_local_state_and_downloaded_ckpt() -> None:
    patterns = {
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert {".git", ".venv", "**/__pycache__", "*.ckpt", "reports", "*.mp4"} <= patterns
    assert "checkpoints/*.onnx" not in patterns


def test_integration_guide_assigns_business_and_algorithm_ownership_explicitly() -> None:
    guide = (PROJECT_ROOT / "docs" / "ALGORITHM_API_INTEGRATION.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "/health/live",
        "/health/ready",
        "/api/v2/portrait/generate",
        "/api/v2/witness/blur-background",
        "/api/v2/verify/images",
        "/api/v2/verify/video/quick",
        "/api/v2/verify/video/full",
        "/api/detect_face",
        "/api/extract_feature",
        "ACCESS_DENIED",
        "THRESHOLD_UNCALIBRATED",
        "calibrated: false",
        "1024",
        "可信参考",
        "合同有效性",
        "异步队列",
        "人工复核",
        "不记录",
        "单 worker",
        "2.25 GiB",
        "3 GiB",
        "按能力拆分",
        "2 GiB",
        "两份",
        "TrustedCalibrationKeyStore",
        "[0, 2]",
        "背景虚化单能力",
        "至少 32 UTF-8 字节",
        "预生产基线",
    ):
        assert required in guide
