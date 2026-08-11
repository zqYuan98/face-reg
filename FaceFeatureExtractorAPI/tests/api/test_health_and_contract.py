from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from api.services import DefaultAlgorithmServices

from .conftest import FakeServices, config_with, encode_jpeg


class _UnreadyServices(FakeServices):
    def readiness(self):
        return {
            "status": "unhealthy",
            "ready": False,
            "models": [],
            "capabilities": {},
        }


def test_liveness_is_independent_from_model_readiness(algorithm_config) -> None:
    from api.app import create_app

    with TestClient(create_app(config=algorithm_config, services=_UnreadyServices())) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        compatibility = client.get("/health")

    assert live.status_code == 200
    assert live.json() == {"status": "live"}
    assert ready.status_code == 503
    assert ready.json()["status"] == "unhealthy"
    assert compatibility.status_code == 503


def test_lifespan_owns_service_start_and_close_once(algorithm_config, fake_services) -> None:
    from api.app import create_app

    with TestClient(create_app(config=algorithm_config, services=fake_services)) as client:
        assert client.get("/health/ready").status_code == 200
        assert fake_services.started == 1
        assert fake_services.closed == 0

    assert fake_services.closed == 1


def test_shutdown_drains_timed_out_algorithm_before_releasing_services(
    algorithm_config,
) -> None:
    class SlowServices(FakeServices):
        def __init__(self) -> None:
            super().__init__()
            self.finished = threading.Event()
            self.closed_after_drain = False

        def verify_images(self, *args, **kwargs):
            time.sleep(0.08)
            self.finished.set()
            return super().verify_images(*args, **kwargs)

        def close(self) -> None:
            self.closed_after_drain = self.finished.is_set()
            super().close()

    from api.app import create_app

    services = SlowServices()
    config = config_with(algorithm_config, image_deadline=0.02)
    upload = ("face.jpg", encode_jpeg(), "image/jpeg")
    with TestClient(create_app(config=config, services=services)) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={"reference_image": upload, "probe_image": upload},
        )
        assert response.status_code == 504

    assert services.closed_after_drain is True


def test_openapi_contains_only_installed_v2_routes_and_no_embedding_schema(
    algorithm_config,
    fake_services,
) -> None:
    from api.app import create_app

    schema = create_app(config=algorithm_config, services=fake_services).openapi()
    paths = schema["paths"]

    assert "/api/v2/portrait/generate" in paths
    assert "/api/v2/verify/images" in paths
    assert "/api/v2/verify/video/quick" in paths
    assert "/api/v2/verify/video/full" in paths
    assert "/api/v2/witness/blur-background" not in paths
    assert "/api/extract_feature" not in paths
    assert "/api/detect_face" in paths
    lowered = str(schema).lower()
    assert "embedding" not in lowered


def test_default_services_advertise_bundled_witness_contract(
    algorithm_config,
) -> None:
    from api.app import create_app

    services = DefaultAlgorithmServices(config=algorithm_config, registry=object())
    schema = create_app(config=algorithm_config, services=services).openapi()

    assert "/api/v2/witness/blur-background" in schema["paths"]


def test_default_cors_policy_does_not_reflect_arbitrary_origins(
    algorithm_config,
    fake_services,
) -> None:
    from api.app import create_app

    with TestClient(create_app(config=algorithm_config, services=fake_services)) as client:
        response = client.options(
            "/api/v2/verify/images",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert "access-control-allow-origin" not in response.headers
