from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from .conftest import config_with, encode_jpeg


TOKEN = "0123456789abcdef0123456789abcdef"


def _app(config, services):
    from api.app import create_app

    return create_app(config=config, services=services)


def _image_file() -> tuple[str, bytes, str]:
    return ("image.jpg", encode_jpeg(), "image/jpeg")


def test_extract_feature_route_is_absent_by_default(algorithm_config, fake_services) -> None:
    app = _app(algorithm_config, fake_services)
    with TestClient(app, client=("10.2.3.4", 12345)) as client:
        response = client.post(
            "/api/extract_feature",
            files={"image": _image_file()},
            headers={"X-Internal-Token": TOKEN},
        )

    assert response.status_code == 404
    assert "/api/extract_feature" not in app.openapi()["paths"]


def test_enabled_extract_route_requires_a_configured_token(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, extract_enabled=True)

    try:
        _app(config, fake_services)
    except ValueError as error:
        assert "legacy token" in str(error).lower()
    else:
        raise AssertionError("enabled extraction without a token must fail closed")


def test_enabled_extract_route_rejects_low_entropy_short_token(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, extract_enabled=True, token="short")

    with pytest.raises(ValueError, match="at least 32"):
        _app(config, fake_services)


def test_default_factory_ignores_yaml_token_and_requires_environment_secret(
    algorithm_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import app as app_module

    config = config_with(algorithm_config, extract_enabled=True, token=TOKEN)
    monkeypatch.setattr(app_module, "load_config", lambda: config)
    monkeypatch.delenv("FACE_ALGO_LEGACY_TOKEN", raising=False)

    with pytest.raises(ValueError, match="legacy token"):
        app_module.create_default_app()


def test_enabled_extract_route_denies_missing_or_wrong_token(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(
        algorithm_config,
        extract_enabled=True,
        token=TOKEN,
    )
    with TestClient(_app(config, fake_services), client=("10.2.3.4", 12345)) as client:
        missing = client.post(
            "/api/extract_feature",
            files={"image": _image_file()},
        )
        wrong = client.post(
            "/api/extract_feature",
            files={"image": _image_file()},
            headers={"X-Internal-Token": "wrong"},
        )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert missing.json() == wrong.json()
    assert missing.json()["reason_codes"] == ["ACCESS_DENIED"]


def test_legacy_authorization_runs_before_required_multipart_validation(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, extract_enabled=True, token=TOKEN)
    with TestClient(_app(config, fake_services), client=("10.2.3.4", 12345)) as client:
        response = client.post(
            "/api/extract_feature",
            headers={"X-Internal-Token": "wrong"},
        )

    assert response.status_code == 403
    assert response.json()["reason_codes"] == ["ACCESS_DENIED"]


def test_legacy_authorization_rejects_before_consuming_multipart_body(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, extract_enabled=True, token=TOKEN)
    boundary = b"legacy-auth-boundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="image"; filename="image.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
        + encode_jpeg()
        + b"\r\n--" + boundary + b"--\r\n"
    )
    consumed_chunks = 0

    async def chunks():
        nonlocal consumed_chunks
        for offset in range(0, len(body), 64):
            consumed_chunks += 1
            yield body[offset : offset + 64]

    async def scenario():
        transport = httpx.ASGITransport(
            app=_app(config, fake_services),
            client=("10.2.3.4", 12345),
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/extract_feature",
                content=chunks(),
                headers={
                    "Content-Type": (
                        f"multipart/form-data; boundary={boundary.decode()}"
                    ),
                    "X-Internal-Token": "wrong",
                },
            )

    response = asyncio.run(scenario())

    assert response.status_code == 403
    assert response.json()["reason_codes"] == ["ACCESS_DENIED"]
    assert consumed_chunks == 0


def test_enabled_extract_route_denies_public_direct_peer_and_ignores_spoofed_xff(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, extract_enabled=True, token=TOKEN)
    with TestClient(_app(config, fake_services), client=("203.0.113.9", 12345)) as client:
        response = client.post(
            "/api/extract_feature",
            files={"image": _image_file()},
            headers={
                "X-Internal-Token": TOKEN,
                "X-Forwarded-For": "10.2.3.4",
            },
        )

    assert response.status_code == 403


def test_trusted_proxy_resolves_allowed_forwarded_client(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, extract_enabled=True, token=TOKEN)
    with TestClient(_app(config, fake_services), client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/extract_feature",
            files={"image": _image_file()},
            headers={
                "X-Internal-Token": TOKEN,
                "X-Forwarded-For": "10.2.3.4",
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["feature_dim"] == 1024
    assert len(response.json()["feature"]) == 1024


def test_malformed_forwarded_chain_fails_closed_for_trusted_proxy(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, extract_enabled=True, token=TOKEN)
    with TestClient(_app(config, fake_services), client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/api/extract_feature",
            files={"image": _image_file()},
            headers={
                "X-Internal-Token": TOKEN,
                "X-Forwarded-For": "10.2.3.4, not-an-ip",
            },
        )

    assert response.status_code == 403


def test_detect_face_compatibility_route_remains_available(
    algorithm_config,
    fake_services,
) -> None:
    with TestClient(_app(algorithm_config, fake_services)) as client:
        response = client.post(
            "/api/detect_face",
            files={"image": _image_file()},
            data={"expand_scale": "0.5"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Success"
    assert len(body["faces"]) == 1
    face = body["faces"][0]
    assert face["x1"] == 0.0
    assert face["y1"] == 0.0
    assert face["x2"] <= 32.0
    assert face["y2"] <= 24.0


@pytest.mark.parametrize("expand_scale", ["NaN", "Infinity", "-0.1", "2.01"])
def test_detect_face_rejects_non_finite_negative_or_excessive_expansion(
    algorithm_config,
    fake_services,
    expand_scale: str,
) -> None:
    with TestClient(_app(algorithm_config, fake_services)) as client:
        response = client.post(
            "/api/detect_face",
            files={"image": _image_file()},
            data={"expand_scale": expand_scale},
        )

    assert response.status_code == 400
    assert response.json()["reason_codes"] == ["INVALID_IMAGE"]
