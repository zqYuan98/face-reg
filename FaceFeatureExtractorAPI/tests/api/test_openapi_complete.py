from __future__ import annotations

import pytest

from api.app import create_app
from api.services import DefaultAlgorithmServices
from .conftest import config_with


def test_presence_revision_version_and_trial_contract(algorithm_config):
    schema = create_app(config=algorithm_config, services=DefaultAlgorithmServices(
        config=algorithm_config, registry=object())).openapi()
    assert schema["info"]["version"] == "2.3.0"
    props = schema["components"]["schemas"]["VideoPresenceResult"]["properties"]
    assert props["calibration_status"]["enum"] == ["UNCALIBRATED", "CONFIGURED_UNVALIDATED"]
    assert {"threshold", "comparison_status", "compared_candidates", "base_frames"} <= set(props)
    description = schema["paths"]["/api/v2/verify/video/presence"]["post"]["description"]
    assert "NOT_FOUND_IN_SCAN" in description
    assert "仅返回 INCONCLUSIVE" not in description


@pytest.mark.parametrize("path", [
    "/api/v2/portrait/generate", "/api/v2/witness/blur-background",
    "/api/v2/verify/images", "/api/v2/verify/video/quick",
    "/api/v2/verify/video/full", "/api/v2/verify/video/presence",
    "/api/detect_face", "/health/live", "/health/ready", "/health",
])
def test_installed_endpoints_document_concrete_success_contract(algorithm_config, path):
    services = DefaultAlgorithmServices(config=algorithm_config, registry=object())
    schema = create_app(config=algorithm_config, services=services).openapi()
    method = "get" if path.startswith("/health") else "post"
    response = schema["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "$ref" in response
    model = schema["components"]["schemas"][response["$ref"].rsplit("/", 1)[-1]]
    assert model.get("properties")


def test_algorithm_openapi_describes_actual_errors_instead_of_framework_422(algorithm_config):
    services = DefaultAlgorithmServices(config=algorithm_config, registry=object())
    schema = create_app(config=algorithm_config, services=services).openapi()
    for path, item in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        responses = item["post"]["responses"]
        assert "422" not in responses
        assert {"400", "413", "500", "503", "504"} <= set(responses)
        assert responses["400"]["content"]["application/json"]["schema"]["$ref"].endswith("/ApiErrorResponse")
    assert "embedding" not in str(schema).lower()


def test_disabled_legacy_feature_remains_hidden_and_enabled_requires_header(algorithm_config):
    services = DefaultAlgorithmServices(config=algorithm_config, registry=object())
    schema = create_app(config=algorithm_config, services=services).openapi()
    assert "/api/extract_feature" not in schema["paths"]
    enabled = config_with(algorithm_config, extract_enabled=True, token="x" * 32)
    schema = create_app(config=enabled, services=services).openapi()
    operation = schema["paths"]["/api/extract_feature"]["post"]
    assert operation.get("security") == [{"InternalToken": []}]
    assert schema["components"]["securitySchemes"]["InternalToken"]["name"] == "X-Internal-Token"


def test_portrait_and_witness_schema_match_wire_fields_not_internal_image(algorithm_config):
    services = DefaultAlgorithmServices(config=algorithm_config, registry=object())
    schema = create_app(config=algorithm_config, services=services).openapi()
    for name in ("PortraitResponse", "WitnessBlurResponse"):
        props = schema["components"]["schemas"].get(name, {}).get("properties", {})
        assert "image_base64" in props and "image" not in props
    assert "feature_version" not in schema["components"]["schemas"]["PortraitResponse"]["properties"]


def test_portrait_archive_keeps_request_and_response_fields(algorithm_config):
    schema = create_app(config=algorithm_config, services=DefaultAlgorithmServices(
        config=algorithm_config, registry=object())).openapi()
    operation = schema["paths"]["/api/v2/portrait/generate"]["post"]
    request = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    request_model = schema["components"]["schemas"][request["$ref"].rsplit("/", 1)[-1]]
    assert set(request_model["properties"]) == {"image"}
    assert request_model["required"] == ["image"]
    response = schema["components"]["schemas"]["PortraitResponse"]["properties"]
    assert set(response) == {
        "success", "suggestion", "reason_codes", "model_versions", "config_version",
        "processing_time_ms", "trace_id", "template_version", "output_width", "output_height",
        "output_format", "jpeg_quality", "original_width", "original_height",
        "rotation_quarter_turns", "placement", "matting_quality", "image_base64",
    }
    assert "REVIEW" in operation["description"]
    assert "肩" in operation["description"]
