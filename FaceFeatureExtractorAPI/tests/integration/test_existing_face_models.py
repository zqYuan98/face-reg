from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest

from face_algorithm.core.adapters import (
    FaceRecognizerAdapter,
    build_face_pipeline,
    create_model_registry,
    get_default_pipeline,
    validate_face_recognizer_contract,
)
from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_registry import ModelContract, load_manifest
from face_algorithm.schemas import HealthLevel, ReasonCode, Suggestion
from face_algorithm.verification.image_verifier import (
    build_image_verification_runtime,
)
from face_feature_extractor import FaceFeatureExtractor
from models.facerecoger import FaceRecoger
from models.imgchecker import QualityOfPose


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "source" / "synthetic_person_holding_blank_card.png"
FIXTURE_SHA256 = "70d1518bbb32ef8c38792870a0dbb9e1a05466354d3f5cbfc0cc470e4b0c5773"
CORE_NAMES = (
    "face_detector",
    "face_landmark_primary",
    "face_landmark_secondary",
    "face_recognizer",
    "rotation_classifier",
    "pose_var",
    "pose_conv",
)
EXPECTED_IO = {
    "face_detector": (["img"], ["loc", "conf"], [[1, 3, 640, 640]], [[1, 8525, 4], [1, 8525, 2]]),
    "face_landmark_primary": (["input1"], ["output1", "output2"], [[1, 1, 112, 112]], [[1, 8, 27, 27], [1, 10]]),
    "face_landmark_secondary": (["input1"], ["output"], [[1, 640, 1, 1]], [[1, 10]]),
    "face_recognizer": (["_input_123"], ["fc1_act_50"], [[None, None, None, None]], [[None, 1024, 1, 1]]),
    "rotation_classifier": (["input"], ["output"], [[1, 3, 224, 224]], [[1, 4]]),
    "pose_var": (["input"], ["output"], [[1, 3, 64, 64]], [[1, 3]]),
    "pose_conv": (["input"], ["output"], [[1, 3, 64, 64]], [[1, 3]]),
}


@pytest.fixture(scope="module")
def config():
    return load_config(PROJECT_ROOT / "config" / "algorithm.yaml")


@pytest.fixture(scope="module")
def registry(config):
    return create_model_registry(PROJECT_ROOT / "models.manifest.json", config)


@pytest.fixture(scope="module")
def fixture_image() -> np.ndarray:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    source_record = (PROJECT_ROOT / "tests" / "fixtures" / "SOURCES.md").read_text(
        encoding="utf-8"
    )
    assert FIXTURE_SHA256 in source_record
    assert "entirely synthetic adult" in source_record
    image = cv2.imdecode(
        np.frombuffer(FIXTURE.read_bytes(), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert image is not None and image.dtype == np.uint8 and image.ndim == 3
    return image


@pytest.mark.integration
def test_all_required_sessions_load_from_verified_bytes_once_on_cpu(registry) -> None:
    sessions = {name: registry.get(name) for name in CORE_NAMES}

    assert all(registry.get(name) is session for name, session in sessions.items())
    for session in sessions.values():
        assert "CUDAExecutionProvider" not in session.get_providers()
        assert session.get_providers()[0] == "CPUExecutionProvider"
    for name, session in sessions.items():
        assert registry.verified_evidence(name).providers == tuple(
            session.get_providers()
        )
    health = registry.health()
    assert health.status is HealthLevel.DEGRADED
    assert all(
        model.state.value == "ready"
        for model in health.models
        if model.name in CORE_NAMES
    )
    assert next(
        model for model in health.models if model.name == "portrait_matting"
    ).state.value == "unavailable"


@pytest.mark.integration
def test_real_onnx_io_contracts_and_raw_recognizer_dummy_inference(registry) -> None:
    for name, expected in EXPECTED_IO.items():
        session = registry.get(name)
        input_names = [node.name for node in session.get_inputs()]
        output_names = [node.name for node in session.get_outputs()]
        input_shapes = [node.shape for node in session.get_inputs()]
        output_shapes = [node.shape for node in session.get_outputs()]
        assert (input_names, output_names, input_shapes, output_shapes) == expected

    recognizer = registry.get("face_recognizer")
    raw = recognizer.run(
        ["fc1_act_50"],
        {"_input_123": np.zeros((1, 3, 248, 248), dtype=np.float32)},
    )[0]
    assert raw.dtype == np.float32
    assert raw.shape == (1, 1024, 1, 1)
    manifest = load_manifest(PROJECT_ROOT / "models.manifest.json")
    artifact_entry = next(entry for entry in manifest.models if entry.name == "face_recognizer")
    artifact = type("Artifact", (), {"entry": artifact_entry})()
    assert validate_face_recognizer_contract(recognizer, artifact) == ModelContract(
        feature_version="recognizer_v1_1024",
        output_dimension=1024,
    )


@pytest.mark.integration
def test_synthetic_fixture_runs_full_real_face_chain_and_keeps_legacy_embedding(
    registry,
    config,
    fixture_image,
) -> None:
    pipeline = build_face_pipeline(config=config, registry=registry)
    assert pipeline.max_image_pixels == config.resources.max_image_pixels
    smoke_profile = config.quality_profiles.entry_verify.model_copy(
        update={
            "brightness": config.quality_profiles.entry_verify.brightness.model_copy(
                update={"v0": 0.0, "v1": 1.0, "v2": 254.0, "v3": 255.0}
            ),
            "clarity": config.quality_profiles.entry_verify.clarity.model_copy(
                update={"low_threshold": 0.001, "high_threshold": 0.002}
            ),
            "pose": config.quality_profiles.entry_verify.pose.model_copy(
                update={
                    "yaw_threshold": 90.0,
                    "pitch_threshold": 90.0,
                    "roll_threshold": 180.0,
                }
            ),
        }
    )

    trusted = pipeline.analyze_trusted(fixture_image, smoke_profile)

    assert trusted.public.suggestion is Suggestion.PASS, trusted.public.model_dump()
    assert trusted.embedding is not None
    assert trusted.embedding.dtype == np.float32
    assert trusted.embedding.shape == (1024,)
    assert np.all(np.isfinite(trusted.embedding))
    assert np.linalg.norm(trusted.embedding) == pytest.approx(1.0, abs=1e-6)
    assert trusted.public.box is not None
    assert trusted.public.landmarks is not None
    assert len(trusted.public.landmarks.points) == 5
    assert trusted.aligned_face is not None

    legacy_pose = QualityOfPose(
        var_session=registry.get("pose_var"),
        conv_session=registry.get("pose_conv"),
    ).detect_angle(trusted.aligned_face)
    assert trusted.public.pose is not None
    assert np.asarray(
        [trusted.public.pose.yaw, trusted.public.pose.pitch, trusted.public.pose.roll]
    ) == pytest.approx(np.asarray(legacy_pose), abs=1e-6)

    old = FaceRecoger(
        onnx_path=None,
        num_threads=config.models.intra_op_num_threads,
        session=registry.get("face_recognizer"),
    )
    legacy_embedding = old.inference(trusted.aligned_face)
    adapter_embedding = FaceRecognizerAdapter(
        registry.get("face_recognizer")
    ).extract(trusted.aligned_face)
    assert np.max(np.abs(legacy_embedding - adapter_embedding)) <= 1e-7


@pytest.mark.integration
def test_side_by_side_real_fixture_is_detected_as_multiple_faces(
    registry,
    config,
    fixture_image,
) -> None:
    pipeline = build_face_pipeline(config=config, registry=registry)
    double = np.concatenate((fixture_image, fixture_image), axis=1)

    result = pipeline.analyze_public(
        double,
        config.quality_profiles.entry_verify,
    )

    assert result.success is True
    assert result.suggestion.value == "RECAPTURE"
    assert result.reason_codes == (ReasonCode.MULTIPLE_FACES,)


@pytest.mark.integration
def test_real_model_same_and_lightly_transformed_synthetic_image_score_but_default_never_passes(
    registry,
    config,
    fixture_image,
) -> None:
    runtime = build_image_verification_runtime(
        registry=registry,
        config=config,
        key_store=None,
    )
    transformed = cv2.convertScaleAbs(fixture_image, alpha=1.0, beta=5)

    same = runtime.verify(fixture_image, fixture_image.copy())
    lightly_transformed = runtime.verify(fixture_image, transformed)

    for result in (same, lightly_transformed):
        assert result.reference.usable is True, result.model_dump()
        assert result.probe.usable is True, result.model_dump()
        assert result.similarity_score is not None
        assert np.isfinite(result.similarity_score)
        assert -1.0 <= result.similarity_score <= 1.0
        assert result.matcher_version == "l2_dot_v1"
        assert result.feature_version.value == "recognizer_v1_1024"
        assert result.feature_dimension == 1024
        assert result.calibration.status == "UNCALIBRATED"
        assert result.success is True
        assert result.suggestion is Suggestion.REVIEW
        assert result.reason_codes == (ReasonCode.THRESHOLD_UNCALIBRATED,)
        assert result.decision_threshold is None


@pytest.mark.integration
def test_verification_runtime_owns_sessions_during_real_registry_drift(
    registry,
    config,
    fixture_image,
) -> None:
    runtime = build_image_verification_runtime(
        registry=registry,
        config=config,
        key_store=None,
    )
    external_session = registry.get("face_recognizer")
    owned_pipeline = runtime._verifier._analyze_trusted.__self__
    owned_session = owned_pipeline.embedder.recognizer.ort_session
    assert owned_session is not external_session

    transformed = cv2.convertScaleAbs(fixture_image, alpha=1.0, beta=5)
    baseline = runtime.verify(fixture_image, transformed)
    assert baseline.similarity_score is not None
    pipeline_fingerprint = runtime.pipeline_fingerprint
    original_run = external_session.run
    had_instance_run = "run" in vars(external_session)
    original_instance_run = vars(external_session).get("run")
    stop = threading.Event()
    drift_count = 0

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("caller-owned registry session must not be used")

    def drift_registry_session() -> None:
        nonlocal drift_count
        while not stop.is_set():
            external_session.run = forbidden_run
            external_session.run = original_run
            drift_count += 1

    mutator = threading.Thread(target=drift_registry_session)
    mutator.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    lambda _index: runtime.verify(fixture_image, transformed),
                    range(8),
                )
            )
    finally:
        stop.set()
        mutator.join(timeout=2.0)
        if had_instance_run:
            external_session.run = original_instance_run
        else:
            del external_session.run

    assert not mutator.is_alive()
    assert drift_count > 0
    assert runtime.pipeline_fingerprint == pipeline_fingerprint
    assert all(result.reference.usable is True for result in results)
    assert all(result.probe.usable is True for result in results)
    assert all(
        result.similarity_score == pytest.approx(
            baseline.similarity_score,
            abs=1e-7,
        )
        for result in results
    )


@pytest.mark.integration
def test_legacy_extractors_share_the_lazy_default_sessions() -> None:
    pipeline = get_default_pipeline()
    first = FaceFeatureExtractor()
    second = FaceFeatureExtractor()

    assert get_default_pipeline() is pipeline
    assert first.pipeline is second.pipeline is pipeline
    assert (
        first.pipeline.embedder.recognizer.ort_session
        is second.pipeline.embedder.recognizer.ort_session
    )
