"""Strict, file-based configuration for the face-algorithm package."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from ipaddress import ip_network
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyNetwork,
    SecretStr,
    field_validator,
    model_validator,
)
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

__all__ = ["resolve_config_path", "load_config"]

_CONFIG_ENVIRONMENT_VARIABLE = "FACE_ALGO_CONFIG"
_SOURCE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "algorithm.yaml"


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            line = key_node.start_mark.line + 1
            column = key_node.start_mark.column + 1
            raise ValueError(f"duplicate key {key!r} at line {line}, column {column}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class ResourceLimits(_StrictModel):
    max_image_bytes: int = Field(gt=0)
    max_image_pixels: int = Field(gt=0)
    max_video_bytes: int = Field(gt=0)
    max_video_duration_seconds: float = Field(gt=0)
    max_video_fps: float = Field(gt=0)
    max_full_video_frames: int = Field(gt=0)


class ModelEntry(_StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend: Literal["onnxruntime", "pytorch"]
    providers: tuple[
        Literal["CPUExecutionProvider", "CUDAExecutionProvider"], ...
    ] = Field(min_length=1)
    enabled: bool = True
    expected_output_dimension: int | None = Field(default=None, gt=0)
    memory_hint_mb: int = Field(gt=0)

    @field_validator("path", mode="before")
    @classmethod
    def convert_path(cls, value: Any) -> Any:
        return Path(value) if isinstance(value, str) else value

    @field_validator("providers", mode="before")
    @classmethod
    def convert_providers(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value


class ModelsConfig(_StrictModel):
    intra_op_num_threads: int = Field(gt=0, le=256)
    face_detector: ModelEntry
    face_landmark_primary: ModelEntry
    face_landmark_secondary: ModelEntry
    face_recognizer: ModelEntry
    rotation_classifier: ModelEntry
    pose_var: ModelEntry
    pose_conv: ModelEntry
    portrait_matting: ModelEntry


class BrightnessThresholds(_StrictModel):
    v0: float = Field(ge=0, le=255)
    v1: float = Field(ge=0, le=255)
    v2: float = Field(ge=0, le=255)
    v3: float = Field(ge=0, le=255)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if not self.v0 <= self.v1 < self.v2 <= self.v3:
            raise ValueError("brightness thresholds must satisfy v0 <= v1 < v2 <= v3")
        return self


class ResolutionLimits(_StrictModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class PoseThresholds(_StrictModel):
    yaw_threshold: float = Field(ge=0, le=90)
    pitch_threshold: float = Field(ge=0, le=90)
    roll_threshold: float = Field(ge=0, le=180)


class ClarityThresholds(_StrictModel):
    low_threshold: float = Field(ge=0, le=1)
    high_threshold: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.low_threshold >= self.high_threshold:
            raise ValueError("clarity low_threshold must be below high_threshold")
        return self


class LandmarkGeometry(_StrictModel):
    require_points_inside_bbox: bool
    bbox_tolerance_ratio: float = Field(ge=0, le=0.25)
    min_eye_distance_ratio: float = Field(gt=0, le=1)
    min_mouth_width_ratio: float = Field(gt=0, le=1)
    max_eye_vertical_difference_ratio: float = Field(ge=0, le=1)


class ExactOneFaceSelection(_StrictModel):
    mode: Literal["exact_one"]


class DominantPrimaryFaceSelection(_StrictModel):
    mode: Literal["dominant_primary"]
    max_face_candidates: int = Field(ge=1, le=5)
    ignore_secondary_area_ratio: float = Field(gt=0, lt=1)
    ambiguous_secondary_area_ratio: float = Field(gt=0, le=1)
    secondary_min_confidence: float = Field(ge=0, le=1)
    selector_version: Literal["dominant-primary-v1"]

    @model_validator(mode="after")
    def validate_area_threshold_order(self) -> Self:
        if (
            self.ignore_secondary_area_ratio
            >= self.ambiguous_secondary_area_ratio
        ):
            raise ValueError(
                "ignore secondary area ratio must be below ambiguous secondary area ratio"
            )
        return self


FaceSelectionConfig = Annotated[
    ExactOneFaceSelection | DominantPrimaryFaceSelection,
    Field(discriminator="mode"),
]


class QualityProfile(_StrictModel):
    face_selection: FaceSelectionConfig
    brightness: BrightnessThresholds
    resolution: ResolutionLimits
    pose: PoseThresholds
    clarity: ClarityThresholds
    border_margin_ratio: float = Field(ge=0, lt=0.5)
    landmark_geometry: LandmarkGeometry

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_face_selection(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "require_single_face" in value:
            raise ValueError(
                "require_single_face was removed; migrate to face_selection.mode"
            )
        return value

    @property
    def require_single_face(self) -> bool:
        """Compatibility view for strict-only internal guards.

        The value is derived from the discriminated strategy and is not a
        configuration field, so ``face_selection`` remains the sole source of
        truth and the legacy YAML key is still rejected.
        """

        return self.face_selection.mode == "exact_one"


class QualityProfiles(_StrictModel):
    portrait: QualityProfile
    witness: QualityProfile
    entry_verify: QualityProfile
    contract_reference: QualityProfile
    contract_video_frame: QualityProfile

    @model_validator(mode="after")
    def validate_face_selection_modes(self) -> Self:
        for profile_name in ("portrait", "witness"):
            if getattr(self, profile_name).face_selection.mode != "dominant_primary":
                raise ValueError(
                    f"{profile_name} face_selection must use dominant_primary"
                )
        for profile_name in (
            "entry_verify",
            "contract_reference",
            "contract_video_frame",
        ):
            if getattr(self, profile_name).face_selection.mode != "exact_one":
                raise ValueError(
                    f"{profile_name} face_selection must use exact_one"
                )
        return self


class PortraitTemplate(_StrictModel):
    version: str = Field(min_length=1)
    output_width: int = Field(gt=0, le=8192)
    output_height: int = Field(gt=0, le=8192)
    output_format: Literal["jpeg", "png"]
    jpeg_quality: int = Field(ge=1, le=100)
    eye_line_y_ratio: float = Field(gt=0, lt=1)
    face_height_ratio: float = Field(gt=0, lt=1)
    top_margin_ratio: float = Field(ge=0, lt=1)
    max_upscale_ratio: float = Field(ge=1, le=8)
    alpha_threshold: float = Field(gt=0, lt=1)
    feather_radius_pixels: int = Field(ge=0, le=64)
    min_face_alpha_mean: float = Field(ge=0, le=1)
    min_face_alpha_coverage: float = Field(ge=0, le=1)
    min_subject_area_ratio: float = Field(gt=0, lt=1)
    edge_alpha_threshold: float = Field(gt=0, lt=1)
    head_roi_expansion_ratio: float = Field(ge=0, le=1)
    top_edge_band_ratio: float = Field(gt=0, le=0.05)
    top_edge_min_pixels: int = Field(ge=1, le=64)
    min_top_edge_core_coverage: float = Field(gt=0, le=1)
    robust_top_min_row_width_ratio: float = Field(gt=0, le=1)
    required_headroom_ratio: float = Field(ge=0, le=1)
    min_shoulder_depth_ratio: float = Field(ge=0, le=5)
    shoulder_roi_width_ratio: float = Field(gt=0, le=5)
    min_shoulder_band_coverage: float = Field(ge=0, le=1)
    min_shoulder_half_coverage: float = Field(ge=0, le=1)
    min_shoulder_valid_row_ratio: float = Field(ge=0, le=1)
    min_shoulder_row_coverage: float = Field(ge=0, le=1)
    background_rgb: tuple[int, int, int]

    @field_validator("background_rgb", mode="before")
    @classmethod
    def convert_background_rgb(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("background_rgb")
    @classmethod
    def validate_background_rgb(
        cls,
        value: tuple[int, int, int],
    ) -> tuple[int, int, int]:
        if any(channel < 0 or channel > 255 for channel in value):
            raise ValueError("background_rgb must contain three values in [0, 255]")
        if value != (255, 255, 255):
            raise ValueError("portrait background_rgb must be pure white")
        return value

    @model_validator(mode="after")
    def validate_alpha_thresholds(self) -> Self:
        if self.edge_alpha_threshold > self.alpha_threshold:
            raise ValueError(
                "edge_alpha_threshold cannot exceed alpha_threshold"
            )
        if self.output_width * self.output_height > 4_194_304:
            raise ValueError(
                "portrait output pixel budget cannot exceed 4194304"
            )
        return self


class WitnessOptions(_StrictModel):
    max_witness_pixels: int = Field(gt=0)
    blur_sigma_ratio: float = Field(gt=0, le=0.25)
    blur_kernel_cap: int = Field(ge=3, le=511)
    blur_working_max_dimension: int = Field(ge=128, le=4096)
    mask_threshold: float = Field(ge=0, le=1)
    subject_edge_alpha: float = Field(gt=0, le=1)
    min_face_alpha_mean: float = Field(ge=0, le=1)
    min_face_alpha_coverage: float = Field(ge=0, le=1)
    min_subject_area_ratio: float = Field(gt=0, lt=1)
    max_subject_area_ratio: float = Field(gt=0, lt=1)
    card_margin_ratio: float = Field(ge=0, le=0.25)
    min_card_area_ratio: float = Field(gt=0, lt=1)
    max_card_area_ratio: float = Field(gt=0, le=1)
    min_card_aspect_ratio: float = Field(gt=0)
    max_card_aspect_ratio: float = Field(gt=0)
    max_card_count: int = Field(ge=1, le=3)
    min_card_width_to_face_ratio: float = Field(gt=0)
    max_card_width_to_face_ratio: float = Field(gt=0)
    min_card_area_to_face_ratio: float = Field(gt=0)
    max_card_area_to_face_ratio: float = Field(gt=0)
    card_detection_max_dimension: int = Field(ge=128, le=4096)
    min_card_short_side_pixels: int = Field(ge=8, le=2048)
    max_card_center_offset_to_face_width_ratio: float = Field(gt=0, le=10)
    min_card_rectangularity: float = Field(gt=0, le=1)
    max_card_angle_deviation_degrees: float = Field(ge=0, lt=90)
    min_card_candidate_confidence: float = Field(gt=0, le=1)
    card_ambiguity_margin: float = Field(gt=0, lt=1)
    card_contact_dilation_ratio: float = Field(ge=0, le=0.25)
    card_nms_iou_threshold: float = Field(gt=0, le=1)
    card_edge_margin_ratio: float = Field(ge=0, le=0.10)
    feather_radius_ratio: float = Field(ge=0, le=0.10)

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if self.blur_kernel_cap % 2 == 0:
            raise ValueError("blur_kernel_cap must be odd")
        if self.subject_edge_alpha > self.mask_threshold:
            raise ValueError("subject_edge_alpha cannot exceed mask_threshold")
        if self.min_subject_area_ratio >= self.max_subject_area_ratio:
            raise ValueError("minimum subject area must be below maximum")
        if self.min_card_area_ratio > self.max_card_area_ratio:
            raise ValueError("minimum card area ratio cannot exceed maximum")
        if self.min_card_aspect_ratio > self.max_card_aspect_ratio:
            raise ValueError("minimum card aspect ratio cannot exceed maximum")
        if self.min_card_width_to_face_ratio > self.max_card_width_to_face_ratio:
            raise ValueError("minimum card/face width ratio cannot exceed maximum")
        if self.min_card_area_to_face_ratio > self.max_card_area_to_face_ratio:
            raise ValueError("minimum card/face area ratio cannot exceed maximum")
        if self.min_card_short_side_pixels > self.card_detection_max_dimension:
            raise ValueError("minimum card short side exceeds detection resolution")
        if self.card_ambiguity_margin > self.min_card_candidate_confidence:
            raise ValueError("card ambiguity margin cannot exceed confidence")
        return self


class UncalibratedImageVerification(_StrictModel):
    mode: Literal["uncalibrated"]
    reference_quality_profile: Literal["entry_verify"]
    probe_quality_profile: Literal["entry_verify"]


class ArtifactImageVerification(_StrictModel):
    mode: Literal["artifact"]
    reference_quality_profile: Literal["entry_verify"]
    probe_quality_profile: Literal["entry_verify"]
    artifact_path: Path
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_path: Path
    signer_key_id: str = Field(min_length=1)

    @field_validator("artifact_path", "signature_path", mode="before")
    @classmethod
    def convert_path(cls, value: Any) -> Any:
        return Path(value) if isinstance(value, str) else value


ImageVerificationConfig = Annotated[
    UncalibratedImageVerification | ArtifactImageVerification,
    Field(discriminator="mode"),
]


class UncalibratedVideoVerification(_StrictModel):
    mode: Literal["uncalibrated"]


class ArtifactVideoVerification(_StrictModel):
    mode: Literal["artifact"]
    artifact_path: Path
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_path: Path
    signer_key_id: str = Field(min_length=1)

    @field_validator("artifact_path", "signature_path", mode="before")
    @classmethod
    def convert_path(cls, value: Any) -> Any:
        return Path(value) if isinstance(value, str) else value


VideoVerificationCalibrationConfig = Annotated[
    UncalibratedVideoVerification | ArtifactVideoVerification,
    Field(discriminator="mode"),
]


class VideoQuickPolicy(_StrictModel):
    sample_count: int = Field(gt=0)
    min_valid_frames: int = Field(gt=0)
    include_first_last: bool
    aggregate_percentile: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_frame_count(self) -> Self:
        if self.min_valid_frames > self.sample_count:
            raise ValueError("quick min_valid_frames cannot exceed sample_count")
        return self


class VideoFullPolicy(_StrictModel):
    sample_interval_seconds: float = Field(gt=0)
    min_valid_frames: int = Field(gt=0)
    max_analyzed_frames: int = Field(gt=0)
    track_iou_threshold: float = Field(ge=0, le=1)
    track_landmark_distance_threshold: float = Field(gt=0)
    track_embedding_threshold: float = Field(ge=0, le=1)
    min_track_coverage: float = Field(ge=0, le=1)
    max_secondary_track_ratio: float = Field(ge=0, le=1)
    max_no_face_gap_seconds: float = Field(ge=0)
    aggregate_percentile: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_frame_count(self) -> Self:
        if self.min_valid_frames > self.max_analyzed_frames:
            raise ValueError("full min_valid_frames cannot exceed max_analyzed_frames")
        return self


class VideoVerificationPolicy(_StrictModel):
    calibration: VideoVerificationCalibrationConfig
    reference_quality_profile: Literal["contract_reference"]
    frame_quality_profile: Literal["contract_video_frame"]
    quick: VideoQuickPolicy
    full: VideoFullPolicy


class RouteDeadlines(_StrictModel):
    image_seconds: float = Field(gt=0)
    video_quick_seconds: float = Field(gt=0)
    video_full_seconds: float = Field(gt=0)


class LegacyRoutes(_StrictModel):
    extract_feature_enabled: bool
    detect_face_enabled: bool
    token: SecretStr | None
    allowed_client_cidrs: tuple[IPvAnyNetwork, ...]
    trusted_proxy_cidrs: tuple[IPvAnyNetwork, ...]

    @field_validator("token", mode="before")
    @classmethod
    def convert_token(cls, value: Any) -> Any:
        return SecretStr(value) if isinstance(value, str) else value

    @field_validator(
        "allowed_client_cidrs",
        "trusted_proxy_cidrs",
        mode="before",
    )
    @classmethod
    def convert_cidrs(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return tuple(
            ip_network(item, strict=False) if isinstance(item, str) else item
            for item in value
        )


class AlgorithmConfig(_StrictModel):
    config_version: str = Field(min_length=1)
    feature_version: Literal["recognizer_v1_1024"]
    resources: ResourceLimits
    models: ModelsConfig
    quality_profiles: QualityProfiles
    portrait_template: PortraitTemplate
    witness: WitnessOptions
    image_verification: ImageVerificationConfig
    video_verification: VideoVerificationPolicy
    route_deadlines: RouteDeadlines
    legacy: LegacyRoutes

    @model_validator(mode="after")
    def validate_cross_section_contracts(self) -> Self:
        if self.models.face_recognizer.expected_output_dimension != 1024:
            raise ValueError("face recognizer output dimension must be 1024")
        if (
            self.video_verification.full.max_analyzed_frames
            > self.resources.max_full_video_frames
        ):
            raise ValueError("full video policy exceeds the resource frame limit")
        if self.witness.max_witness_pixels > self.resources.max_image_pixels:
            raise ValueError("witness pixel limit exceeds the shared image limit")
        return self


def _default_config_path() -> Path:
    if _SOURCE_CONFIG_PATH.is_file():
        return _SOURCE_CONFIG_PATH
    return Path(sys.prefix) / "config" / "algorithm.yaml"


def _normalize_paths(payload: Any, config_path: Path) -> Any:
    if not isinstance(payload, dict):
        return payload
    models = payload.get("models")
    if isinstance(models, dict):
        for model_payload in models.values():
            if not isinstance(model_payload, dict) or "path" not in model_payload:
                continue
            configured_path = model_payload["path"]
            if isinstance(configured_path, str):
                configured_path = Path(configured_path)
            if isinstance(configured_path, Path) and not configured_path.is_absolute():
                configured_path = (config_path.parent / configured_path).resolve()
            model_payload["path"] = configured_path

    image_verification = payload.get("image_verification")
    if isinstance(image_verification, dict):
        for field_name in ("artifact_path", "signature_path"):
            configured_path = image_verification.get(field_name)
            if isinstance(configured_path, str):
                configured_path = Path(configured_path)
            if isinstance(configured_path, Path) and not configured_path.is_absolute():
                configured_path = (config_path.parent / configured_path).resolve()
            if configured_path is not None:
                image_verification[field_name] = configured_path

    video_verification = payload.get("video_verification")
    video_calibration = (
        video_verification.get("calibration")
        if isinstance(video_verification, dict)
        else None
    )
    if isinstance(video_calibration, dict):
        for field_name in ("artifact_path", "signature_path"):
            configured_path = video_calibration.get(field_name)
            if isinstance(configured_path, str):
                configured_path = Path(configured_path)
            if isinstance(configured_path, Path) and not configured_path.is_absolute():
                configured_path = (config_path.parent / configured_path).resolve()
            if configured_path is not None:
                video_calibration[field_name] = configured_path
    return payload


def resolve_config_path(explicit: Path | None = None) -> Path:
    """Resolve explicit path, then ``FACE_ALGO_CONFIG``, then the package default."""

    if explicit is not None:
        candidate = explicit
    else:
        configured = os.environ.get(_CONFIG_ENVIRONMENT_VARIABLE)
        candidate = Path(configured) if configured else _default_config_path()
    return Path(candidate).expanduser().resolve()


def load_config(explicit: Path | None = None) -> AlgorithmConfig:
    """Load and strictly validate the selected YAML configuration file."""

    path = resolve_config_path(explicit)
    with path.open("r", encoding="utf-8") as stream:
        loader = _UniqueKeySafeLoader(stream)
        try:
            payload = loader.get_single_data()
        finally:
            loader.dispose()
    normalized_payload = _normalize_paths(payload, path)
    return AlgorithmConfig.model_validate(normalized_payload)
