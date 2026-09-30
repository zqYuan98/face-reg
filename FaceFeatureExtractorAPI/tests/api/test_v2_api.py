from __future__ import annotations

import base64
import asyncio
import threading
import time
from dataclasses import replace
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from face_algorithm.runtime.model_registry import ModelUnavailableError
from face_algorithm.runtime.calibration import CalibrationLoadError
from face_algorithm.schemas import ImageVerificationResult, ReasonCode, Suggestion

from .conftest import (
    FakeServices,
    config_with,
    encode_jpeg,
    portrait_result,
    witness_result,
)


def _client(config, services) -> TestClient:
    from api.app import create_app

    return TestClient(create_app(config=config, services=services))


def _image_file(name: str = "image.jpg") -> tuple[str, bytes, str]:
    return (name, encode_jpeg(), "image/jpeg")


def _jpeg_with_exif_orientation(orientation: int) -> bytes:
    jpeg = encode_jpeg(width=40, height=20)
    tiff = (
        b"II\x2a\x00\x08\x00\x00\x00"
        b"\x01\x00"
        b"\x12\x01\x03\x00\x01\x00\x00\x00"
        + orientation.to_bytes(2, "little")
        + b"\x00\x00"
        b"\x00\x00\x00\x00"
    )
    exif = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    return jpeg[:2] + app1 + jpeg[2:]


def _assert_no_private_payload(value) -> None:
    forbidden = {"embedding", "embeddings", "feature", "features", "alpha", "matte", "raw_image", "raw_frame"}
    if isinstance(value, dict):
        assert forbidden.isdisjoint(value)
        for child in value.values():
            _assert_no_private_payload(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_private_payload(child)


def test_portrait_route_returns_only_encoded_derived_image(algorithm_config, fake_services) -> None:
    with _client(algorithm_config, fake_services) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={"image": _image_file()},
        )

    assert response.status_code == 200
    body = response.json()
    encoded = base64.b64decode(body.pop("image_base64"), validate=True)
    decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None and decoded.shape == (12, 10, 3)
    assert body["success"] is True
    _assert_no_private_payload(body)


def test_derived_image_encoding_runs_inside_single_algorithm_owner(
    algorithm_config,
    fake_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = _image_file()
    original = cv2.imencode
    encoding_threads: list[str] = []

    def observed_imencode(*args, **kwargs):
        encoding_threads.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr("api.serialization.cv2.imencode", observed_imencode)
    with _client(algorithm_config, fake_services) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={"image": upload},
        )

    assert response.status_code == 200
    assert encoding_threads
    assert all(name.startswith("face-algorithm-owner") for name in encoding_threads)


def test_derived_image_output_pixel_budget_is_enforced_before_encoding(
    algorithm_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedOutputServices(FakeServices):
        def generate_portrait(self, image, *, deadline_monotonic):
            return portrait_result(np.zeros((11, 10, 3), dtype=np.uint8))

    config = config_with(algorithm_config, max_image_pixels=100)
    upload = ("small.jpg", encode_jpeg(width=8, height=8), "image/jpeg")
    called = False

    def forbidden_encode(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("oversized derived image must not be encoded")

    monkeypatch.setattr("api.serialization.cv2.imencode", forbidden_encode)
    with _client(config, OversizedOutputServices()) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={"image": upload},
        )

    assert response.status_code == 500
    assert called is False


def test_derived_image_encoded_byte_budget_is_enforced(
    algorithm_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_with(algorithm_config, max_image_bytes=1024)
    upload = _image_file()

    def oversized_encode(*args, **kwargs):
        return True, np.zeros(1025, dtype=np.uint8)

    monkeypatch.setattr("api.serialization.cv2.imencode", oversized_encode)
    with _client(config, FakeServices()) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={"image": upload},
        )

    assert response.status_code == 500
    assert response.json()["reason_codes"] == ["INFERENCE_ERROR"]


def test_forged_exact_portrait_nested_mapping_fails_closed(
    algorithm_config,
) -> None:
    class ForgedPortraitServices(FakeServices):
        def generate_portrait(self, image, *, deadline_monotonic):
            valid = portrait_result(np.zeros((10, 10, 3), dtype=np.uint8))
            return replace(
                valid,
                placement={"debug_vector": [0.125] * 1023},
            )

    with _client(algorithm_config, ForgedPortraitServices()) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={"image": _image_file()},
        )

    assert response.status_code == 500
    assert "0.125" not in response.text


def test_image_verification_never_returns_embedding_or_source_media(algorithm_config, fake_services) -> None:
    with _client(algorithm_config, fake_services) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )

    assert response.status_code == 200
    assert response.json()["suggestion"] == "REVIEW"
    assert response.json()["feature_dimension"] == 1024
    _assert_no_private_payload(response.json())


@pytest.mark.parametrize(
    ("route", "expected_mode"),
    [
        ("/api/v2/verify/video/quick", "quick"),
        ("/api/v2/verify/video/full", "full"),
    ],
)
def test_video_routes_stream_to_private_tempfile_and_always_remove_it(
    algorithm_config,
    fake_services,
    route: str,
    expected_mode: str,
) -> None:
    with _client(algorithm_config, fake_services) as client:
        response = client.post(
            route,
            files={
                "reference_image": _image_file("reference.jpg"),
                "video": ("capture.mp4", b"fake-video", "video/mp4"),
            },
        )

    assert response.status_code == 200
    assert response.json()["mode"] == expected_mode
    _assert_no_private_payload(response.json())
    assert fake_services.video_paths
    assert all(not path.exists() for path in fake_services.video_paths)


def test_witness_route_is_not_advertised_without_an_installed_adapter(
    algorithm_config,
    fake_services,
) -> None:
    with _client(algorithm_config, fake_services) as client:
        assert client.post(
            "/api/v2/witness/blur-background",
            files={"image": _image_file()},
        ).status_code == 404
        assert "/api/v2/witness/blur-background" not in client.get("/openapi.json").json()["paths"]


def test_witness_route_rejects_adapter_output_outside_concrete_contract(
    algorithm_config,
    fake_services,
) -> None:
    fake_services.blur_witness_background = lambda *args, **kwargs: object()
    with _client(algorithm_config, fake_services) as client:
        response = client.post(
            "/api/v2/witness/blur-background",
            files={"image": _image_file()},
        )

        assert response.status_code == 500
        assert response.json()["reason_codes"] == ["INFERENCE_ERROR"]
        assert "/api/v2/witness/blur-background" in client.get("/openapi.json").json()["paths"]


def test_witness_route_returns_only_encoded_blurred_image_and_safe_summary(
    algorithm_config,
) -> None:
    class WitnessServices(FakeServices):
        def blur_witness_background(self, image, *, deadline_monotonic):
            assert image.shape == (24, 32, 3)
            self.deadlines.append(("witness", deadline_monotonic))
            return witness_result(np.full_like(image, 96))

    services = WitnessServices()
    with _client(algorithm_config, services) as client:
        response = client.post(
            "/api/v2/witness/blur-background",
            files={"image": _image_file("witness.jpg")},
        )
        paths = client.get("/openapi.json").json()["paths"]

    assert response.status_code == 200
    body = response.json()
    encoded = base64.b64decode(body.pop("image_base64"), validate=True)
    decoded = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None and decoded.shape == (24, 32, 3)
    assert body["success"] is True
    assert body["card_candidate_count"] == 2
    assert len(body["card_regions"]) == 1
    assert body["coordinate_space"] == "original_image"
    assert "/api/v2/witness/blur-background" in paths
    _assert_no_private_payload(body)


def test_portrait_review_success_keeps_encoded_image_and_existing_fields(
    algorithm_config, fake_services, monkeypatch,
) -> None:
    result = replace(portrait_result(np.full((12, 10, 3), 255, dtype=np.uint8)),
                     suggestion=Suggestion.REVIEW, reason_codes=(ReasonCode.TOO_DARK,))
    monkeypatch.setattr(fake_services, "generate_portrait", lambda *args, **kwargs: result)
    with _client(algorithm_config, fake_services) as client:
        response = client.post("/api/v2/portrait/generate", files={"image": _image_file()})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and body["suggestion"] == "REVIEW"
    assert body["reason_codes"] == ["TOO_DARK"]
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(body["image_base64"], validate=True),
                                        dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (12, 10, 3)
    assert "quality_warnings" not in body
    assert {"shoulder_depth_ratio", "shoulder_band_coverage"} <= body["matting_quality"].keys()
    _assert_no_private_payload(body)


def test_witness_encoding_uses_bounded_quality_ladder(
    algorithm_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WitnessServices(FakeServices):
        def blur_witness_background(self, image, *, deadline_monotonic):
            return witness_result(np.full_like(image, 96))

    config = config_with(algorithm_config, max_image_bytes=1024)
    upload = _image_file("witness.jpg")
    qualities: list[int] = []

    def sized_encode(_extension, _image, parameters):
        quality = int(parameters[1])
        qualities.append(quality)
        size = 1025 if quality > 84 else 1000
        return True, np.zeros(size, dtype=np.uint8)

    monkeypatch.setattr("api.serialization.cv2.imencode", sized_encode)
    with _client(config, WitnessServices()) as client:
        response = client.post(
            "/api/v2/witness/blur-background",
            files={"image": upload},
        )

    assert response.status_code == 200
    assert qualities == [92, 88, 84]
    assert len(base64.b64decode(response.json()["image_base64"], validate=True)) == 1000


def test_witness_encoding_exhaustion_is_http_413_not_contract_500(
    algorithm_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WitnessServices(FakeServices):
        def blur_witness_background(self, image, *, deadline_monotonic):
            return witness_result(np.full_like(image, 96))

    config = config_with(algorithm_config, max_image_bytes=1024)
    upload = _image_file("witness.jpg")
    qualities: list[int] = []

    def oversized_encode(_extension, _image, parameters):
        qualities.append(int(parameters[1]))
        return True, np.zeros(1025, dtype=np.uint8)

    monkeypatch.setattr("api.serialization.cv2.imencode", oversized_encode)
    with _client(config, WitnessServices()) as client:
        response = client.post(
            "/api/v2/witness/blur-background",
            files={"image": upload},
        )

    assert response.status_code == 413
    assert response.json()["reason_codes"] == ["IMAGE_TOO_LARGE"]
    assert qualities == [92, 88, 84, 80, 76]


def test_witness_pixel_limit_is_rejected_from_header_before_decode_or_adapter(
    algorithm_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WitnessServices(FakeServices):
        def __init__(self) -> None:
            super().__init__()
            self.called = False

        def blur_witness_background(self, image, *, deadline_monotonic):
            self.called = True
            raise AssertionError("oversized witness image must not reach adapter")

    config = algorithm_config.model_copy(
        update={
            "witness": algorithm_config.witness.model_copy(
                update={"max_witness_pixels": 100}
            )
        }
    )
    # Valid PNG signature/IHDR declares 11 x 10 = 110 pixels.
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (11).to_bytes(4, "big")
        + (10).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    decoded = False

    def forbidden_decode(*args, **kwargs):
        nonlocal decoded
        decoded = True
        raise AssertionError("header limit must run before decode")

    monkeypatch.setattr(cv2, "imdecode", forbidden_decode)
    services = WitnessServices()
    with _client(config, services) as client:
        response = client.post(
            "/api/v2/witness/blur-background",
            files={"image": ("witness.png", payload, "image/png")},
        )

    assert response.status_code == 413
    assert response.json()["reason_codes"] == ["IMAGE_TOO_LARGE"]
    assert decoded is False
    assert services.called is False


@pytest.mark.parametrize(
    ("files", "expected_reason"),
    [
        ({"image": ("bad.gif", b"GIF89a", "image/gif")}, "UNSUPPORTED_FORMAT"),
        ({"image": ("bad.jpg", b"\xff\xd8broken", "image/jpeg")}, "INVALID_IMAGE"),
    ],
)
def test_malformed_or_unsupported_image_is_http_400(
    algorithm_config,
    fake_services,
    files,
    expected_reason,
) -> None:
    with _client(algorithm_config, fake_services) as client:
        response = client.post("/api/v2/portrait/generate", files=files)

    assert response.status_code == 400
    assert response.json()["reason_codes"] == [expected_reason]


@pytest.mark.parametrize("orientation", [6, 8])
def test_mobile_exif_orientation_is_ignored_before_rotation_classifier(
    algorithm_config,
    orientation: int,
) -> None:
    class ShapeServices(FakeServices):
        def __init__(self) -> None:
            super().__init__()
            self.shape = None

        def generate_portrait(self, image, *, deadline_monotonic):
            self.shape = image.shape
            return portrait_result(np.zeros((10, 10, 3), dtype=np.uint8))

    services = ShapeServices()
    with _client(algorithm_config, services) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={
                "image": (
                    "phone.jpg",
                    _jpeg_with_exif_orientation(orientation),
                    "image/jpeg",
                )
            },
        )

    assert response.status_code == 200
    assert services.shape == (20, 40, 3)


def test_image_upload_limit_is_http_413_before_algorithm_call(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, max_image_bytes=32)
    with _client(config, fake_services) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={"image": ("large.jpg", b"x" * 33, "image/jpeg")},
        )

    assert response.status_code == 413
    assert response.json()["reason_codes"] == ["IMAGE_TOO_LARGE"]
    assert not fake_services.deadlines


def test_png_header_pixel_limit_is_checked_before_opencv_decode(
    algorithm_config,
    fake_services,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_with(algorithm_config, max_image_pixels=100)
    # Valid signature/IHDR dimensions are sufficient for the pre-decode rejection.
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (1000).to_bytes(4, "big")
        + (1000).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    called = False

    def forbidden_decode(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("decoder must not run")

    monkeypatch.setattr(cv2, "imdecode", forbidden_decode)
    with _client(config, fake_services) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={"image": ("bomb.png", payload, "image/png")},
        )

    assert response.status_code == 413
    assert response.json()["reason_codes"] == ["IMAGE_TOO_LARGE"]
    assert called is False


def test_video_upload_limit_is_http_413_and_tempfile_is_removed(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, max_video_bytes=4)
    with _client(config, fake_services) as client:
        response = client.post(
            "/api/v2/verify/video/quick",
            files={
                "reference_image": _image_file("reference.jpg"),
                "video": ("capture.mp4", b"12345", "video/mp4"),
            },
        )

    assert response.status_code == 413
    assert response.json()["reason_codes"] == ["VIDEO_TOO_LARGE"]
    assert not fake_services.video_paths


def test_total_multipart_body_is_rejected_before_extra_fields_are_spooled(
    algorithm_config,
    fake_services,
) -> None:
    config = config_with(algorithm_config, max_image_bytes=1024)
    with _client(config, fake_services) as client:
        response = client.post(
            "/api/v2/portrait/generate",
            files={
                "image": _image_file(),
                "ignored": ("ignored.bin", b"x" * (70 * 1024), "application/octet-stream"),
            },
        )

    assert response.status_code == 413
    assert response.json()["reason_codes"] == ["IMAGE_TOO_LARGE"]
    assert not fake_services.deadlines


def test_chunked_multipart_without_content_length_is_still_bounded(
    algorithm_config,
    fake_services,
) -> None:
    from api.app import create_app

    config = config_with(algorithm_config, max_image_bytes=1024)
    boundary = b"face-boundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="image"; filename="image.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
        + encode_jpeg()
        + b"\r\n--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="ignored"; filename="ignored.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + b"x" * (70 * 1024)
        + b"\r\n--" + boundary + b"--\r\n"
    )

    async def chunks():
        for offset in range(0, len(body), 1024):
            yield body[offset : offset + 1024]

    async def scenario():
        transport = httpx.ASGITransport(app=create_app(config=config, services=fake_services))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v2/portrait/generate",
                content=chunks(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 413
    assert response.json()["reason_codes"] == ["IMAGE_TOO_LARGE"]


def test_upload_time_is_included_in_absolute_route_deadline(
    algorithm_config,
    fake_services,
) -> None:
    from api.app import create_app

    config = config_with(algorithm_config, image_deadline=0.02)
    boundary = b"slow-upload-boundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="image"; filename="image.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
        + encode_jpeg()
        + b"\r\n--" + boundary + b"--\r\n"
    )

    async def chunks():
        await asyncio.sleep(0.05)
        yield body

    async def scenario():
        transport = httpx.ASGITransport(app=create_app(config=config, services=fake_services))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v2/portrait/generate",
                content=chunks(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
            )

    response = asyncio.run(scenario())

    assert response.status_code == 504
    assert response.json()["reason_codes"] == ["ALGORITHM_TIMEOUT"]
    assert not fake_services.deadlines


class _FailingVideoServices(FakeServices):
    def verify_video(self, reference_image, video_path, **kwargs):
        self.video_paths.append(video_path)
        assert video_path.is_file()
        raise HTTPException(status_code=409, detail="stop")


def test_video_tempfile_is_removed_when_adapter_raises(algorithm_config) -> None:
    services = _FailingVideoServices()
    with _client(algorithm_config, services) as client:
        response = client.post(
            "/api/v2/verify/video/quick",
            files={
                "reference_image": _image_file("reference.jpg"),
                "video": ("capture.mp4", b"fake-video", "video/mp4"),
            },
        )

    assert response.status_code == 409
    assert services.video_paths
    assert all(not path.exists() for path in services.video_paths)


class _SlowVideoServices(FakeServices):
    def __init__(self) -> None:
        super().__init__()
        self.finished = threading.Event()
        self.path_existed_until_return = False

    def verify_video(self, reference_image, video_path, **kwargs):
        self.video_paths.append(video_path)
        assert video_path.is_file()
        time.sleep(0.08)
        self.path_existed_until_return = video_path.is_file()
        self.finished.set()
        return {
            "success": True,
            "suggestion": "REVIEW",
            "reason_codes": ["THRESHOLD_UNCALIBRATED"],
            "mode": kwargs["mode"],
        }


def test_timed_out_video_keeps_tempfile_until_background_worker_drains(
    algorithm_config,
) -> None:
    services = _SlowVideoServices()
    config = config_with(algorithm_config, quick_deadline=0.02)
    with _client(config, services) as client:
        response = client.post(
            "/api/v2/verify/video/quick",
            files={
                "reference_image": _image_file("reference.jpg"),
                "video": ("capture.mp4", b"fake-video", "video/mp4"),
            },
        )
        assert response.status_code == 504
        assert services.finished.wait(timeout=1.0)
        assert services.path_existed_until_return is True
        deadline = time.monotonic() + 1.0
        while any(path.exists() for path in services.video_paths):
            assert time.monotonic() < deadline
            time.sleep(0.01)

    assert services.video_paths
    assert all(not path.exists() for path in services.video_paths)


class _UnsafeTensorServices(FakeServices):
    def verify_images(self, *args, **kwargs):
        return {
            "success": True,
            "suggestion": "REVIEW",
            "reason_codes": ["THRESHOLD_UNCALIBRATED"],
            "debug_tensor": np.asarray([123456], dtype=np.int64),
        }


def test_unknown_ndarray_output_fails_closed_instead_of_stringifying_pixels(
    algorithm_config,
) -> None:
    with _client(algorithm_config, _UnsafeTensorServices()) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )

    assert response.status_code == 500
    assert "123456" not in response.text


@pytest.mark.parametrize(
    "unsafe_field",
    [
        {"debug_vector": [0.125] * 1024},
        {"frame": [11, 22, 33]},
    ],
)
def test_unexpected_mapping_fields_fail_closed_at_v2_dto_boundary(
    algorithm_config,
    unsafe_field,
) -> None:
    class UnsafeMappingServices(FakeServices):
        def verify_images(self, *args, **kwargs):
            return {
                "success": True,
                "suggestion": "REVIEW",
                "reason_codes": ["THRESHOLD_UNCALIBRATED"],
                **unsafe_field,
            }

    with _client(algorithm_config, UnsafeMappingServices()) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )

    assert response.status_code == 500
    assert "0.125" not in response.text
    assert "11" not in response.text


def test_model_construct_cannot_bypass_exact_pydantic_result_validation(
    algorithm_config,
) -> None:
    class ForgedPydanticServices(FakeServices):
        def verify_images(self, *args, **kwargs):
            return ImageVerificationResult.model_construct(
                success=True,
                reference={"debug_vector": [0.125] * 1023},
            )

    with _client(algorithm_config, ForgedPydanticServices()) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )

    assert response.status_code == 500
    assert "0.125" not in response.text


class _ModelUnavailableServices(FakeServices):
    def verify_images(self, *args, **kwargs):
        raise ModelUnavailableError(
            "face_recognizer",
            ReasonCode.MODEL_UNAVAILABLE,
            "sensitive filesystem detail",
        )


def test_model_unavailable_maps_to_safe_http_503(algorithm_config) -> None:
    with _client(algorithm_config, _ModelUnavailableServices()) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )

    assert response.status_code == 503
    assert response.json()["reason_codes"] == ["MODEL_UNAVAILABLE"]
    assert "filesystem" not in response.text


class _CalibrationUnavailableServices(FakeServices):
    def verify_images(self, *args, **kwargs):
        raise CalibrationLoadError("sensitive signature path")


def test_calibration_failure_maps_to_safe_http_503(algorithm_config) -> None:
    with _client(algorithm_config, _CalibrationUnavailableServices()) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )

    assert response.status_code == 503
    assert response.json()["reason_codes"] == ["CALIBRATION_REQUIRED"]
    assert "signature" not in response.text


class _HttpExceptionServices(FakeServices):
    def verify_images(self, *args, **kwargs):
        raise HTTPException(status_code=409, detail="caller-visible conflict")


def test_http_exception_from_adapter_is_not_swallowed(algorithm_config) -> None:
    with _client(algorithm_config, _HttpExceptionServices()) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "caller-visible conflict"


class _SlowServices(FakeServices):
    def __init__(self) -> None:
        super().__init__()
        self.finished = threading.Event()
        self.calls = 0

    def verify_images(self, *args, **kwargs):
        self.calls += 1
        time.sleep(0.08)
        self.finished.set()
        return super().verify_images(*args, **kwargs)


def test_wall_clock_timeout_returns_504_and_worker_drains_before_reuse(
    algorithm_config,
) -> None:
    services = _SlowServices()
    config = config_with(algorithm_config, image_deadline=0.02)
    with _client(config, services) as client:
        response = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )
        assert response.status_code == 504
        assert response.json()["reason_codes"] == ["ALGORITHM_TIMEOUT"]
        assert services.finished.wait(timeout=1.0)


def test_timed_out_worker_keeps_runtime_slot_until_underlying_call_finishes(
    algorithm_config,
) -> None:
    services = _SlowServices()
    config = config_with(algorithm_config, image_deadline=0.02)
    with _client(config, services) as client:
        first = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )
        second = client.post(
            "/api/v2/verify/images",
            files={
                "reference_image": _image_file("reference.jpg"),
                "probe_image": _image_file("probe.jpg"),
            },
        )
        assert first.status_code == 504
        assert second.status_code == 504
        assert services.calls == 1
        assert services.finished.wait(timeout=1.0)


def test_each_route_receives_its_absolute_deadline(algorithm_config, fake_services) -> None:
    config = config_with(
        algorithm_config,
        image_deadline=3.0,
        quick_deadline=5.0,
        full_deadline=7.0,
    )
    before = time.monotonic()
    with _client(config, fake_services) as client:
        client.post("/api/v2/portrait/generate", files={"image": _image_file()})
        for mode in ("quick", "full"):
            client.post(
                f"/api/v2/verify/video/{mode}",
                files={
                    "reference_image": _image_file("reference.jpg"),
                    "video": ("capture.mp4", b"fake-video", "video/mp4"),
                },
            )

    by_name = dict(fake_services.deadlines)
    assert before + 2.5 <= by_name["portrait"] <= before + 3.5
    assert before + 4.5 <= by_name["quick"] <= before + 5.5
    assert before + 6.5 <= by_name["full"] <= before + 7.5
