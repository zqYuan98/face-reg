from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import psutil
import pytest
import torch

from face_algorithm.core.adapters import create_model_registry
from face_algorithm.portrait import build_portrait_matting_runtime
from face_algorithm.runtime.config import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "source"
    / "synthetic_person_holding_blank_card.png"
)
FIXTURE_SHA256 = (
    "70d1518bbb32ef8c38792870a0dbb9e1a05466354d3f5cbfc0cc470e4b0c5773"
)


@pytest.mark.integration
def test_real_pinned_modnet_returns_nonconstant_original_size_alpha() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    image = cv2.imdecode(
        np.frombuffer(FIXTURE.read_bytes(), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert image is not None
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    models = config.models.model_copy(
        update={
            "portrait_matting": config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    config = config.model_copy(update={"models": models})
    registry = create_model_registry(PROJECT_ROOT / "models.manifest.json", config)
    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()

    runtime = build_portrait_matting_runtime(registry=registry, config=config)
    alpha = runtime.matte(image)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    rss_delta_mb = (process.memory_info().rss - rss_before) / (1024 * 1024)
    stats = {
        "alpha_min": float(alpha.min()),
        "alpha_max": float(alpha.max()),
        "alpha_mean": float(alpha.mean()),
        "alpha_std": float(alpha.std()),
        "elapsed_ms_including_load": elapsed_ms,
        "rss_delta_mb": rss_delta_mb,
    }
    print(json.dumps(stats, sort_keys=True))

    assert alpha.shape == image.shape[:2]
    assert alpha.dtype == np.float32
    assert alpha.flags.c_contiguous
    assert np.all(np.isfinite(alpha))
    assert 0.0 <= float(alpha.min()) < float(alpha.max()) <= 1.0
    assert float(alpha.std()) > 0.01
    assert runtime.model_info.version == (
        "28165a451e4610c9d77cfdf925a94610bb2810fb"
    )
    assert runtime.model_info.sha256 == (
        "7c22235f0925deba15d4d63e53afcb654c47055bbcd98f56e393ab2584007ed8"
    )
    assert runtime.model_info.backend == "pytorch"
    assert runtime.model_info.providers == ("CPUExecutionProvider",)
    assert registry.get("portrait_matting").get_providers() == (
        "CPUExecutionProvider",
    )


@pytest.mark.integration
def test_real_portrait_runtime_isolated_from_source_registry_model_drift() -> None:
    config = load_config(PROJECT_ROOT / "config" / "algorithm.yaml")
    models = config.models.model_copy(
        update={
            "portrait_matting": config.models.portrait_matting.model_copy(
                update={"enabled": True}
            )
        }
    )
    config = config.model_copy(update={"models": models})
    registry = create_model_registry(PROJECT_ROOT / "models.manifest.json", config)
    runtime = build_portrait_matting_runtime(registry=registry, config=config)
    source_adapter = registry.get("portrait_matting")
    owned_adapter = runtime._matte.__self__
    assert owned_adapter is not source_adapter

    image = np.zeros((64, 64, 3), dtype=np.uint8)
    baseline = runtime.matte(image)
    original_model = source_adapter._model
    stop = threading.Event()
    drift_count = 0

    class ConstantModel(torch.nn.Module):
        def forward(self, tensor, inference):
            assert inference is True
            matte = torch.full(
                (1, 1, tensor.shape[2], tensor.shape[3]),
                0.9375,
                dtype=torch.float32,
            )
            return None, None, matte

    replacement = ConstantModel().eval()

    def drift_source_model() -> None:
        nonlocal drift_count
        while not stop.is_set():
            source_adapter._model = replacement
            source_adapter._model = original_model
            drift_count += 1

    mutator = threading.Thread(target=drift_source_model)
    mutator.start()
    try:
        results = [runtime.matte(image) for _ in range(4)]
    finally:
        stop.set()
        mutator.join(timeout=2.0)
        source_adapter._model = original_model

    assert not mutator.is_alive()
    assert drift_count > 0
    for alpha in results:
        np.testing.assert_allclose(alpha, baseline, rtol=0.0, atol=1e-6)
