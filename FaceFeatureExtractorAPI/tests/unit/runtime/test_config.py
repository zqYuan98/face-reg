from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from copy import deepcopy
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr, ValidationError

from face_algorithm.runtime import config as config_module
from face_algorithm.runtime.config import load_config, resolve_config_path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "algorithm.yaml"


def _default_payload() -> dict:
    payload = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_config(path: Path, payload: dict) -> Path:
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _derived_config(tmp_path: Path, version: str) -> Path:
    payload = deepcopy(_default_payload())
    payload["config_version"] = version
    return _write_config(tmp_path / f"{version}.yaml", payload)


def _model_entries(config: Any) -> list[Any]:
    models = config.models
    return [
        getattr(models, name)
        for name in type(models).model_fields
        if name != "intra_op_num_threads"
    ]


def _production_python_sources(project_root: Path) -> list[Path]:
    sources: list[Path] = []
    for package_name in ("face_algorithm", "models", "api"):
        package_root = project_root / package_name
        if package_root.is_dir():
            sources.extend(sorted(package_root.rglob("*.py")))
    for module_name in ("face_feature_extractor.py", "feature_server.py"):
        module_path = project_root / module_name
        if module_path.is_file():
            sources.append(module_path)
    return sources


def _old_config_literal_references(source_paths: Iterable[Path]) -> list[str]:
    offenders: list[str] = []
    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and Path(node.value).name == "config.yaml"
            ):
                offenders.append(f"{source_path}:{node.lineno}")
    return offenders


def test_only_loader_functions_are_public() -> None:
    assert config_module.__all__ == ["resolve_config_path", "load_config"]


def test_explicit_path_beats_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = _derived_config(tmp_path, "explicit")
    environment = _derived_config(tmp_path, "environment")
    monkeypatch.setenv("FACE_ALGO_CONFIG", str(environment))

    assert resolve_config_path(explicit) == explicit.resolve()
    assert load_config(explicit).config_version == "explicit"


def test_environment_path_beats_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment = _derived_config(tmp_path, "environment")
    monkeypatch.setenv("FACE_ALGO_CONFIG", str(environment))

    assert resolve_config_path() == environment.resolve()
    assert load_config().config_version == "environment"


def test_default_path_is_package_relative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACE_ALGO_CONFIG", raising=False)

    assert resolve_config_path() == DEFAULT_CONFIG.resolve()
    config = load_config()
    assert config.config_version == "2026-08-24-v1"
    assert config.resources.max_video_fps == 120.0


def test_default_path_falls_back_to_sys_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "installed-prefix"
    expected = prefix / "config" / "algorithm.yaml"
    expected.parent.mkdir(parents=True)
    expected.write_text(DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.delenv("FACE_ALGO_CONFIG", raising=False)
    monkeypatch.setattr(
        config_module,
        "_SOURCE_CONFIG_PATH",
        tmp_path / "missing-source" / "algorithm.yaml",
        raising=False,
    )
    monkeypatch.setattr(sys, "prefix", str(prefix))

    assert resolve_config_path() == expected.resolve()
    config = load_config()
    assert config.config_version == "2026-08-24-v1"
    assert config.models.face_detector.path == (
        prefix / "checkpoints" / "faceboxesv2-640x640.onnx"
    ).resolve()


def test_default_model_paths_are_absolute_outside_project_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FACE_ALGO_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    entries = _model_entries(load_config())

    assert len(entries) == 8
    assert all(entry.path.is_absolute() for entry in entries)
    enabled_entries = [entry for entry in entries if entry.enabled]
    assert len(enabled_entries) == 7
    assert all(entry.path.is_file() for entry in enabled_entries)


def test_explicit_config_resolves_relative_model_paths_from_its_directory(
    tmp_path: Path,
) -> None:
    config_directory = tmp_path / "portable-config"
    config_directory.mkdir()
    absolute_model = (tmp_path / "absolute-recognizer.onnx").resolve()
    payload = deepcopy(_default_payload())
    payload["models"]["face_detector"]["path"] = "weights/detector.onnx"
    payload["models"]["face_recognizer"]["path"] = str(absolute_model)
    config_path = _write_config(config_directory / "algorithm.yaml", payload)

    config = load_config(config_path)

    assert config.models.face_detector.path == (
        config_directory / "weights" / "detector.onnx"
    ).resolve()
    assert config.models.face_recognizer.path == absolute_model


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["resources"]["unbounded_uploads"] = True
    invalid = _write_config(tmp_path / "unknown.yaml", payload)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_config(invalid)


def test_uncalibrated_verification_is_allowed_but_marked() -> None:
    config = load_config(DEFAULT_CONFIG)

    assert config.image_verification.mode == "uncalibrated"
    assert config.image_verification.reference_quality_profile == "entry_verify"
    assert config.image_verification.probe_quality_profile == "entry_verify"
    assert not hasattr(config.image_verification, "calibrated")
    assert not hasattr(config.image_verification, "threshold")
    assert not hasattr(config.image_verification, "threshold_version")
    assert config.video_verification.calibration.mode == "uncalibrated"
    assert not hasattr(config.video_verification, "calibrated")
    assert not hasattr(config.video_verification, "frame_threshold")
    assert not hasattr(config.video_verification, "threshold_version")
    assert not hasattr(config.video_verification.quick, "aggregate_threshold")
    assert not hasattr(config.video_verification.quick, "min_pass_ratio")
    assert not hasattr(config.video_verification.full, "aggregate_threshold")
    assert not hasattr(config.video_verification.full, "min_pass_ratio")


def test_legacy_image_verification_fields_are_rejected(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    payload["image_verification"].update(
        calibrated=True,
        threshold=0.70,
        threshold_version="unsafe-v1",
    )
    invalid = _write_config(tmp_path / "legacy-image-policy.yaml", payload)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_config(invalid)


def test_artifact_image_verification_paths_resolve_from_config_directory(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    payload["image_verification"] = {
        "mode": "artifact",
        "reference_quality_profile": "entry_verify",
        "probe_quality_profile": "entry_verify",
        "artifact_path": "calibration/image.json",
        "expected_sha256": "a" * 64,
        "signature_path": "calibration/image.sig",
        "signer_key_id": "calibration-release-v1",
    }

    config_path = _write_config(tmp_path / "algorithm.yaml", payload)
    config = load_config(config_path)

    assert config.image_verification.mode == "artifact"
    assert config.image_verification.artifact_path == (
        tmp_path / "calibration" / "image.json"
    ).resolve()
    assert config.image_verification.signature_path == (
        tmp_path / "calibration" / "image.sig"
    ).resolve()


@pytest.mark.parametrize(
    "image_verification",
    [
        {"mode": "invalid", "reference_quality_profile": "entry_verify", "probe_quality_profile": "entry_verify"},
        {"mode": "artifact", "reference_quality_profile": "entry_verify", "probe_quality_profile": "entry_verify"},
        {"mode": "uncalibrated", "reference_quality_profile": "entry_verify", "probe_quality_profile": "entry_verify", "unexpected": True},
    ],
)
def test_image_verification_union_is_strict(
    tmp_path: Path,
    image_verification: dict,
) -> None:
    payload = deepcopy(_default_payload())
    payload["image_verification"] = image_verification

    with pytest.raises(ValidationError):
        load_config(_write_config(tmp_path / "invalid-image-policy.yaml", payload))


def test_legacy_video_threshold_fields_are_rejected(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["video_verification"].update(calibrated=True, frame_threshold=0.70)
    payload["video_verification"]["quick"]["aggregate_threshold"] = 0.70
    payload["video_verification"]["quick"]["min_pass_ratio"] = 0.70

    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_config(_write_config(tmp_path / "video-placeholder.yaml", payload))


def test_artifact_video_calibration_paths_resolve_from_config_directory(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    payload["video_verification"]["calibration"] = {
        "mode": "artifact",
        "artifact_path": "calibration/video.json",
        "expected_sha256": "b" * 64,
        "signature_path": "calibration/video.sig",
        "signer_key_id": "calibration-release-v1",
    }

    config_path = _write_config(tmp_path / "algorithm.yaml", payload)
    config = load_config(config_path)

    assert config.video_verification.calibration.mode == "artifact"
    assert config.video_verification.calibration.artifact_path == (
        tmp_path / "calibration" / "video.json"
    ).resolve()
    assert config.video_verification.calibration.signature_path == (
        tmp_path / "calibration" / "video.sig"
    ).resolve()


def test_contract_video_frame_must_require_exactly_one_face(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["quality_profiles"]["contract_video_frame"]["face_selection"] = {
        "mode": "dominant_primary",
        "max_face_candidates": 5,
        "ignore_secondary_area_ratio": 0.10,
        "ambiguous_secondary_area_ratio": 0.20,
        "secondary_min_confidence": 0.70,
        "selector_version": "dominant-primary-v1",
    }

    with pytest.raises(ValidationError, match="contract_video_frame.*exact_one"):
        load_config(_write_config(tmp_path / "unsafe-video-profile.yaml", payload))


def test_legacy_require_single_face_is_rejected_with_migration_error(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    profile = payload["quality_profiles"]["entry_verify"]
    profile["require_single_face"] = True

    with pytest.raises(ValidationError, match="require_single_face.*face_selection"):
        load_config(_write_config(tmp_path / "legacy-face-selection.yaml", payload))


@pytest.mark.parametrize("profile_name", ["portrait", "witness"])
def test_field_profiles_require_dominant_primary(
    tmp_path: Path,
    profile_name: str,
) -> None:
    payload = deepcopy(_default_payload())
    payload["quality_profiles"][profile_name]["face_selection"] = {
        "mode": "exact_one"
    }

    with pytest.raises(ValidationError, match=rf"{profile_name}.*dominant_primary"):
        load_config(_write_config(tmp_path / f"strict-{profile_name}.yaml", payload))


@pytest.mark.parametrize(
    "profile_name",
    ["entry_verify", "contract_reference", "contract_video_frame"],
)
def test_identity_profiles_require_exact_one(
    tmp_path: Path,
    profile_name: str,
) -> None:
    payload = deepcopy(_default_payload())
    payload["quality_profiles"][profile_name]["face_selection"] = deepcopy(
        payload["quality_profiles"]["portrait"]["face_selection"]
    )

    with pytest.raises(ValidationError, match=rf"{profile_name}.*exact_one"):
        load_config(_write_config(tmp_path / f"dominant-{profile_name}.yaml", payload))


def test_exact_one_rejects_dominant_only_fields(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["quality_profiles"]["entry_verify"]["face_selection"] = {
        "mode": "exact_one",
        "max_face_candidates": 5,
    }

    with pytest.raises(ValidationError, match="extra_forbidden"):
        load_config(_write_config(tmp_path / "mixed-selection.yaml", payload))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_face_candidates", 6, "less than or equal to 5"),
        ("ignore_secondary_area_ratio", 0.0, "greater than 0"),
        ("ambiguous_secondary_area_ratio", 1.01, "less than or equal to 1"),
        ("secondary_min_confidence", 1.01, "less than or equal to 1"),
        ("selector_version", "unknown", "dominant-primary-v1"),
    ],
)
def test_dominant_primary_thresholds_are_strictly_validated(
    tmp_path: Path,
    field: str,
    value: Any,
    message: str,
) -> None:
    payload = deepcopy(_default_payload())
    payload["quality_profiles"]["portrait"]["face_selection"][field] = value

    with pytest.raises(ValidationError, match=message):
        load_config(_write_config(tmp_path / f"invalid-{field}.yaml", payload))


def test_dominant_primary_area_thresholds_must_be_ordered(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    selection = payload["quality_profiles"]["portrait"]["face_selection"]
    selection["ignore_secondary_area_ratio"] = 0.20
    selection["ambiguous_secondary_area_ratio"] = 0.20

    with pytest.raises(ValidationError, match="ignore.*below.*ambiguous"):
        load_config(_write_config(tmp_path / "unordered-selection.yaml", payload))


def test_full_video_allows_min_valid_frames_equal_to_maximum(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["video_verification"]["full"]["min_valid_frames"] = 20
    payload["video_verification"]["full"]["max_analyzed_frames"] = 20

    config = load_config(_write_config(tmp_path / "full-equal.yaml", payload))

    assert config.video_verification.full.min_valid_frames == 20
    assert config.video_verification.full.max_analyzed_frames == 20


def test_full_video_rejects_min_valid_frames_above_maximum(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["video_verification"]["full"]["min_valid_frames"] = 21
    payload["video_verification"]["full"]["max_analyzed_frames"] = 20
    invalid = _write_config(tmp_path / "full-invalid.yaml", payload)

    with pytest.raises(ValidationError, match="min_valid_frames cannot exceed"):
        load_config(invalid)


@pytest.mark.parametrize(
    ("section", "field", "invalid_value"),
    [
        ("resources", "max_image_bytes", 0),
        ("resources", "max_video_fps", 0),
        ("route_deadlines", "image_seconds", -1),
    ],
)
def test_invalid_threshold_or_resource_limit_is_rejected(
    tmp_path: Path,
    section: str,
    field: str,
    invalid_value: float,
) -> None:
    payload = deepcopy(_default_payload())
    payload[section][field] = invalid_value
    invalid = _write_config(tmp_path / f"invalid-{field}.yaml", payload)

    with pytest.raises(ValidationError):
        load_config(invalid)


def test_strict_numeric_field_rejects_string(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["resources"]["max_image_bytes"] = "15728640"
    invalid = _write_config(tmp_path / "numeric-string.yaml", payload)

    with pytest.raises(ValidationError, match="int_type"):
        load_config(invalid)


def test_strict_boolean_field_rejects_integer(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["legacy"]["extract_feature_enabled"] = 1
    invalid = _write_config(tmp_path / "integer-boolean.yaml", payload)

    with pytest.raises(ValidationError, match="bool_type"):
        load_config(invalid)


def test_loaded_config_is_frozen_and_collections_are_tuples(tmp_path: Path) -> None:
    payload = deepcopy(_default_payload())
    payload["legacy"]["token"] = "internal-secret"
    config = load_config(_write_config(tmp_path / "immutable.yaml", payload))

    assert isinstance(config.models.face_detector.path, Path)
    assert isinstance(config.models.face_detector.providers, tuple)
    assert isinstance(config.portrait_template.background_rgb, tuple)
    assert isinstance(config.legacy.allowed_client_cidrs, tuple)
    assert isinstance(config.legacy.allowed_client_cidrs[0], (IPv4Network, IPv6Network))
    assert isinstance(config.legacy.trusted_proxy_cidrs, tuple)
    assert isinstance(config.legacy.token, SecretStr)
    assert config.legacy.token.get_secret_value() == "internal-secret"

    with pytest.raises(ValidationError, match="frozen_instance"):
        config.resources.max_image_bytes = 1
    with pytest.raises(TypeError):
        config.models.face_detector.providers[0] = "CUDAExecutionProvider"
    with pytest.raises(TypeError):
        config.portrait_template.background_rgb[0] = 0
    with pytest.raises(TypeError):
        config.legacy.allowed_client_cidrs[0] = IPv4Network("127.0.0.1/32")


def test_portrait_geometry_and_alpha_quality_limits_are_explicit() -> None:
    template = load_config(DEFAULT_CONFIG).portrait_template

    assert template.output_width == 480
    assert template.output_height == 640
    assert template.eye_line_y_ratio == pytest.approx(0.40)
    assert template.face_height_ratio == pytest.approx(0.48)
    assert template.max_upscale_ratio == pytest.approx(1.50)
    assert template.min_face_alpha_mean == pytest.approx(0.70)
    assert template.min_face_alpha_coverage == pytest.approx(0.80)
    assert template.min_subject_area_ratio == pytest.approx(0.05)
    assert template.edge_alpha_threshold == pytest.approx(0.10)
    assert template.source_edge_margin_ratio == pytest.approx(0.002)
    assert template.min_shoulder_depth_ratio == pytest.approx(0.60)
    assert template.min_shoulder_band_coverage == pytest.approx(0.35)
    assert not hasattr(template, "shoulder_margin_ratio")


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("max_upscale_ratio", 0.0),
        ("min_face_alpha_mean", 1.01),
        ("min_face_alpha_coverage", -0.01),
        ("min_subject_area_ratio", 1.0),
        ("edge_alpha_threshold", 0.0),
        ("source_edge_margin_ratio", 0.5),
        ("min_shoulder_depth_ratio", -0.01),
        ("min_shoulder_band_coverage", 1.01),
    ],
)
def test_portrait_quality_limits_are_strictly_validated(
    tmp_path: Path,
    field: str,
    invalid_value: float,
) -> None:
    payload = deepcopy(_default_payload())
    payload["portrait_template"][field] = invalid_value

    with pytest.raises(ValidationError):
        load_config(_write_config(tmp_path / f"invalid-portrait-{field}.yaml", payload))


def test_portrait_background_contract_rejects_non_white_configuration(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    payload["portrait_template"]["background_rgb"] = [254, 255, 255]

    with pytest.raises(ValidationError, match="pure white"):
        load_config(_write_config(tmp_path / "non-white-portrait.yaml", payload))


def test_portrait_edge_alpha_threshold_cannot_exceed_subject_threshold(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    payload["portrait_template"]["edge_alpha_threshold"] = 0.75

    with pytest.raises(ValidationError, match="edge_alpha_threshold"):
        load_config(_write_config(tmp_path / "invalid-edge-threshold.yaml", payload))


def test_portrait_alpha_and_shoulder_limits_allow_exact_upper_boundaries(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    payload["portrait_template"]["edge_alpha_threshold"] = 0.50
    payload["portrait_template"]["min_shoulder_band_coverage"] = 1.0

    template = load_config(
        _write_config(tmp_path / "portrait-exact-boundaries.yaml", payload)
    ).portrait_template

    assert template.edge_alpha_threshold == template.alpha_threshold
    assert template.min_shoulder_band_coverage == 1.0


def test_portrait_template_allows_exact_resource_hardening_boundaries(
    tmp_path: Path,
) -> None:
    payload = deepcopy(_default_payload())
    payload["portrait_template"].update(
        output_width=2048,
        output_height=2048,
        feather_radius_pixels=64,
    )

    template = load_config(
        _write_config(tmp_path / "portrait-resource-boundary.yaml", payload)
    ).portrait_template

    assert template.output_width == 2048
    assert template.output_height == 2048
    assert template.feather_radius_pixels == 64


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"output_width": 2049, "output_height": 2048}, "output pixel budget"),
        ({"feather_radius_pixels": 65}, "less than or equal to 64"),
    ],
)
def test_portrait_template_rejects_resource_amplification(
    tmp_path: Path,
    updates: dict[str, int],
    message: str,
) -> None:
    payload = deepcopy(_default_payload())
    payload["portrait_template"].update(updates)

    with pytest.raises(ValidationError, match=message):
        load_config(_write_config(tmp_path / "unsafe-portrait-resource.yaml", payload))


def test_duplicate_top_level_yaml_key_is_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate-top-level.yaml"
    duplicate.write_text(
        DEFAULT_CONFIG.read_text(encoding="utf-8")
        + "\nconfig_version: duplicate-version\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"duplicate key 'config_version'"):
        load_config(duplicate)


def test_duplicate_nested_yaml_key_is_rejected(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate-nested.yaml"
    source = DEFAULT_CONFIG.read_text(encoding="utf-8")
    duplicate.write_text(
        source.replace(
            "  mode: uncalibrated\n",
            "  mode: uncalibrated\n  mode: artifact\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"duplicate key 'mode'"):
        load_config(duplicate)


def test_algorithm_fields_are_not_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FACE_ALGO_CONFIG", raising=False)
    monkeypatch.setenv("FACE_ALGO__IMAGE_VERIFICATION__MODE", "artifact")

    assert load_config().image_verification.mode == "uncalibrated"


def test_all_live_legacy_values_were_migrated() -> None:
    config = load_config(DEFAULT_CONFIG)
    quality = config.quality_profiles.entry_verify

    assert config.image_verification.mode == "uncalibrated"
    assert quality.brightness.model_dump() == {
        "v0": 69.0,
        "v1": 70.0,
        "v2": 230.0,
        "v3": 231.0,
    }
    assert quality.resolution.model_dump() == {"width": 112, "height": 112}
    assert quality.pose.yaw_threshold == pytest.approx(30.0)
    assert quality.pose.pitch_threshold == pytest.approx(25.0)
    assert quality.clarity.low_threshold == pytest.approx(0.10)
    assert quality.clarity.high_threshold == pytest.approx(0.20)
    assert quality.border_margin_ratio == pytest.approx(0.02)
    assert quality.landmark_geometry.require_points_inside_bbox is True
    assert quality.landmark_geometry.min_eye_distance_ratio == pytest.approx(0.15)
    assert quality.landmark_geometry.min_mouth_width_ratio == pytest.approx(0.10)
    assert quality.landmark_geometry.max_eye_vertical_difference_ratio == pytest.approx(0.20)
    assert config.models.intra_op_num_threads == 4
    assert config.models.face_landmark_primary.path.as_posix().endswith(
        "checkpoints/face_landmarker_pts5_net1.onnx"
    )
    assert config.models.face_landmark_secondary.path.as_posix().endswith(
        "checkpoints/face_landmarker_pts5_net2.onnx"
    )
    assert config.models.face_recognizer.path.as_posix().endswith(
        "checkpoints/face_recognizer.onnx"
    )
    assert config.models.face_detector.path.as_posix().endswith(
        "checkpoints/faceboxesv2-640x640.onnx"
    )
    assert config.models.rotation_classifier.path.as_posix().endswith(
        "checkpoints/model_gray_mobilenetv2_rotcls.onnx"
    )
    assert config.models.pose_var.path.as_posix().endswith("checkpoints/fsanet-var.onnx")
    assert config.models.pose_conv.path.as_posix().endswith("checkpoints/fsanet-conv.onnx")


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("border_margin_ratio", 0.5),
        ("bbox_tolerance_ratio", -0.01),
        ("min_eye_distance_ratio", 1.01),
        ("min_mouth_width_ratio", 0.0),
        ("max_eye_vertical_difference_ratio", 1.01),
    ],
)
def test_quality_border_and_landmark_geometry_are_strictly_validated(
    tmp_path: Path,
    field: str,
    invalid_value: float,
) -> None:
    payload = deepcopy(_default_payload())
    profile = payload["quality_profiles"]["entry_verify"]
    if field == "border_margin_ratio":
        profile[field] = invalid_value
    else:
        profile["landmark_geometry"][field] = invalid_value
    invalid = _write_config(tmp_path / f"invalid-{field}.yaml", payload)

    with pytest.raises(ValidationError):
        load_config(invalid)


def test_old_config_scanner_catches_top_level_production_module(tmp_path: Path) -> None:
    fixture = tmp_path / "face_feature_extractor.py"
    fixture.write_text(
        'with open("config.yaml", encoding="utf-8"):\n    pass\n',
        encoding="utf-8",
    )

    sources = _production_python_sources(tmp_path)

    assert sources == [fixture]
    assert _old_config_literal_references(sources) == [f"{fixture}:1"]


def test_production_modules_do_not_open_old_config_yaml() -> None:
    sources = _production_python_sources(PROJECT_ROOT)

    assert PROJECT_ROOT / "face_feature_extractor.py" in sources
    assert PROJECT_ROOT / "feature_server.py" in sources
    assert any(PROJECT_ROOT / "face_algorithm" in source.parents for source in sources)
    assert any(PROJECT_ROOT / "models" in source.parents for source in sources)
    assert _old_config_literal_references(sources) == []
    assert not (PROJECT_ROOT / "config.yaml").exists()
