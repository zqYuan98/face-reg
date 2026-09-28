from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.body_limit import multipart_body_limits
from api.deadline import algorithm_route_deadlines
from api.services import DefaultAlgorithmServices
from face_algorithm.schemas import ReasonCode

from .conftest import FakeServices, config_with, encode_jpeg

PRESENCE_PATH = "/api/v2/verify/video/presence"


class PresenceServices(FakeServices):
    def verify_video_presence(self, reference_image, video_path, *, deadline_monotonic):
        assert video_path.is_file()
        self.video_paths.append(video_path)
        self.deadlines.append(("presence", deadline_monotonic))
        return presence_result()


def presence_result():
    from face_algorithm.presence.contracts import PresenceEvidenceFrame, VideoPresenceResult
    from face_algorithm.schemas import FeatureVersion, Suggestion

    return VideoPresenceResult(
        success=True, suggestion=Suggestion.REVIEW,
        reason_codes=(ReasonCode.CALIBRATION_REQUIRED, ReasonCode.THRESHOLD_UNCALIBRATED),
        model_versions={"face_recognizer": "test-v1"},
        feature_version=FeatureVersion.RECOGNIZER_V1_1024,
        config_version="test-config-v1", processing_time_ms=1.0, trace_id="presence-test",
        best_score=0.8, best_timestamp_seconds=0.5,
        evidence_frames=(PresenceEvidenceFrame(frame_index=15, timestamp_seconds=0.5, score=0.8),),
        scan_complete=True, sampling_interval_seconds=0.5, sampled_frames=3,
        decoded_frames=31, valid_face_frames=1, candidate_count=2,
        candidate_limit_hit=False, duration_seconds=1.0, timestamp_source="pts",
    )


def presence_files():
    return {"reference_image": ("reference.jpg", encode_jpeg(), "image/jpeg"),
            "video": ("capture.mp4", b"fake-video", "video/mp4")}


def test_presence_upload_limit_is_registered_before_multipart_parsing():
    limits = multipart_body_limits(max_image_bytes=100, max_video_bytes=1000)
    assert PRESENCE_PATH in limits
    assert limits[PRESENCE_PATH] == limits["/api/v2/verify/video/full"]


def test_presence_deadline_uses_full_video_budget():
    deadlines = algorithm_route_deadlines(
        image_seconds=30, video_quick_seconds=60, video_full_seconds=600
    )
    assert deadlines.get(PRESENCE_PATH) == 600


def test_presence_has_specific_response_schema_and_two_required_uploads(algorithm_config):
    schema = create_app(config=algorithm_config, services=PresenceServices()).openapi()
    assert PRESENCE_PATH in schema["paths"]
    operation = schema["paths"][PRESENCE_PATH]["post"]
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response.get("$ref", "").endswith("/VideoPresenceResult")
    body_ref = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    body = schema["components"]["schemas"][body_ref.rsplit("/", 1)[-1]]
    assert set(body["required"]) == {"reference_image", "video"}
    assert set(body["properties"]) == {"reference_image", "video"}


def test_presence_service_is_independent_from_old_video_mode(algorithm_config):
    services = DefaultAlgorithmServices(config=algorithm_config, registry=object())
    assert callable(getattr(services, "verify_video_presence", None))


def test_presence_budget_reasons_are_public_contract():
    assert getattr(ReasonCode, "CANDIDATE_LIMIT_REACHED", None) is not None
    assert getattr(ReasonCode, "VIDEO_SCAN_LIMIT_REACHED", None) is not None


def test_presence_default_policy_available_without_changing_old_video_policy(algorithm_config):
    assert hasattr(algorithm_config, "video_presence")
    assert algorithm_config.video_presence.mode == "uncalibrated"
    assert algorithm_config.video_presence.video_min_face_size == 64
    assert algorithm_config.quality_profiles.contract_video_frame.resolution.width == 112


def test_presence_result_is_json_safe_and_upload_is_cleaned(algorithm_config):
    services = PresenceServices()
    with TestClient(create_app(config=algorithm_config, services=services)) as client:
        response = client.post(PRESENCE_PATH, files=presence_files())
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "INCONCLUSIVE" and body["found"] is None
    assert body["best_score"] == 0.8 and body["evidence_frames"][0]["frame_index"] == 15
    assert "embedding" not in response.text.lower()
    assert services.video_paths and all(not p.exists() for p in services.video_paths)


@pytest.mark.parametrize("missing", ["reference_image", "video"])
def test_presence_requires_both_uploads(algorithm_config, missing):
    files = presence_files()
    del files[missing]
    with TestClient(create_app(config=algorithm_config, services=PresenceServices())) as client:
        response = client.post(PRESENCE_PATH, files=files)
    assert response.status_code == 400


def test_presence_does_not_use_image_or_quick_timeout(algorithm_config):
    class SlowPresence(PresenceServices):
        def verify_video_presence(self, *args, **kwargs):
            time.sleep(0.03)
            return super().verify_video_presence(*args, **kwargs)

    config = config_with(algorithm_config, image_deadline=0.001, quick_deadline=0.001, full_deadline=5)
    with TestClient(create_app(config=config, services=SlowPresence())) as client:
        response = client.post(PRESENCE_PATH, files=presence_files())
    assert response.status_code == 200


def test_presence_rejects_arbitrary_private_result(algorithm_config):
    class BadPresence(PresenceServices):
        def verify_video_presence(self, *args, **kwargs):
            return {"embedding": [0.456789] * 1024}

    with TestClient(create_app(config=algorithm_config, services=BadPresence())) as client:
        response = client.post(PRESENCE_PATH, files=presence_files())
    assert response.status_code == 500
    assert "0.456789" not in response.text


def test_legacy_config_without_presence_respects_a_smaller_shared_budget(algorithm_config):
    from face_algorithm.runtime.config import AlgorithmConfig

    old = algorithm_config.model_dump(mode="python")
    del old["video_presence"]
    old["resources"]["max_full_video_frames"] = 100
    old["video_verification"]["full"]["max_analyzed_frames"] = 100
    loaded = AlgorithmConfig.model_validate(old)
    assert loaded.video_presence.max_analyzed_frames <= 100


@pytest.mark.parametrize("kind,reason,status", [
    ("input", ReasonCode.VIDEO_TOO_LARGE, 413),
    ("input", ReasonCode.VIDEO_TOO_LONG, 413),
    ("input", ReasonCode.VIDEO_SCAN_LIMIT_REACHED, 413),
    ("input", ReasonCode.INVALID_VIDEO, 400),
    ("deadline", ReasonCode.ALGORITHM_TIMEOUT, 504),
])
def test_presence_controlled_media_errors_have_safe_http_contract(algorithm_config, kind, reason, status):
    from face_algorithm.video.decoder import VideoDeadlineExceeded, VideoInputError

    class ErrorPresence(PresenceServices):
        def verify_video_presence(self, reference_image, video_path, *, deadline_monotonic):
            self.video_paths.append(video_path)
            cls = VideoDeadlineExceeded if kind == "deadline" else VideoInputError
            raise cls(reason, "private-server-filename-must-not-leak")

    services = ErrorPresence()
    with TestClient(create_app(config=algorithm_config, services=services), raise_server_exceptions=False) as client:
        response = client.post(PRESENCE_PATH, files=presence_files())
    assert response.status_code == status
    assert response.json()["reason_codes"] == [reason.value]
    assert "private-server-filename" not in response.text
    assert services.video_paths and all(not p.exists() for p in services.video_paths)


def test_presence_timeout_keeps_upload_until_owned_work_finishes(algorithm_config):
    import threading

    entered = threading.Event()
    release = threading.Event()

    class WaitingPresence(PresenceServices):
        def verify_video_presence(self, reference_image, video_path, *, deadline_monotonic):
            self.video_paths.append(video_path)
            entered.set()
            release.wait(2)
            assert video_path.exists()
            return presence_result()

    services = WaitingPresence()
    config = config_with(algorithm_config, full_deadline=0.15)
    with TestClient(create_app(config=config, services=services)) as client:
        try:
            response = client.post(PRESENCE_PATH, files=presence_files())
            assert response.status_code == 504 and entered.is_set()
            assert services.video_paths[0].exists()
            busy = client.post(PRESENCE_PATH, files=presence_files())
            assert busy.status_code == 504
            assert len(services.video_paths) == 1
        finally:
            release.set()
    assert not services.video_paths[0].exists()


def test_presence_service_owns_lazy_runtime_and_releases_source_sessions(algorithm_config, monkeypatch):
    import numpy as np
    from .test_services import _Registry
    import face_algorithm.presence as presence_module

    registry = _Registry()
    services = DefaultAlgorithmServices(config=algorithm_config, registry=registry)
    built = []

    class Runtime:
        def verify(self, reference, path, *, deadline_monotonic):
            return presence_result()

    def build(**kwargs):
        built.append(kwargs)
        return Runtime()

    monkeypatch.setattr(presence_module, "build_video_presence_runtime", build)
    services.start()
    assert services.readiness()["capabilities"]["video_presence"] == "ready"
    for _ in range(2):
        result = services.verify_video_presence(np.zeros((1, 1, 3), np.uint8), Path("unused.mp4"), deadline_monotonic=time.monotonic()+5)
        assert result.found is None
    assert len(built) == 1
    assert len(registry.reset_names) == 2 * len(registry.manifest.models)
    services.close()
    assert services._video_presence is None
