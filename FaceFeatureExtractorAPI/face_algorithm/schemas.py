"""Public, privacy-safe schemas shared by the algorithm modules."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Literal

from typing_extensions import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

__all__ = [
    "FEATURE_DIMENSION",
    "FEATURE_DIMENSIONS",
    "IMAGE_RECAPTURE_REASON_CODES",
    "VIDEO_RECAPTURE_REASON_CODES",
    "CardRegion",
    "CalibrationSummary",
    "FaceBox",
    "FaceAnalysisResult",
    "FaceLandmarks",
    "FeatureVersion",
    "FrameSummary",
    "HealthLevel",
    "HealthStatus",
    "ImageCalibrationSummary",
    "ImageVerificationResult",
    "ImageVerificationSide",
    "LandmarkPoint",
    "ModelHealth",
    "ModelState",
    "Pose",
    "QualityMetric",
    "QualityResult",
    "ReasonCode",
    "ResultBase",
    "Suggestion",
    "VideoVerificationResult",
]


class Suggestion(str, Enum):
    """Only outcomes the algorithm may suggest to a business caller."""

    PASS = "PASS"
    RECAPTURE = "RECAPTURE"
    REVIEW = "REVIEW"


class ReasonCode(str, Enum):
    INVALID_FEATURE = "INVALID_FEATURE"
    FEATURE_PAYLOAD_TOO_LARGE = "FEATURE_PAYLOAD_TOO_LARGE"
    FEATURE_SEARCH_BUSY = "FEATURE_SEARCH_BUSY"
    FEATURE_SEARCH_UNAVAILABLE = "FEATURE_SEARCH_UNAVAILABLE"
    INVALID_IMAGE = "INVALID_IMAGE"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    FACE_TOO_SMALL = "FACE_TOO_SMALL"
    FACE_CROPPED = "FACE_CROPPED"
    TOO_DARK = "TOO_DARK"
    TOO_BRIGHT = "TOO_BRIGHT"
    TOO_BLURRY = "TOO_BLURRY"
    BAD_POSE = "BAD_POSE"
    LANDMARK_FAILED = "LANDMARK_FAILED"
    ALIGNMENT_FAILED = "ALIGNMENT_FAILED"
    FEATURE_FAILED = "FEATURE_FAILED"
    FEATURE_VERSION_MISMATCH = "FEATURE_VERSION_MISMATCH"
    THRESHOLD_UNCALIBRATED = "THRESHOLD_UNCALIBRATED"
    IMAGE_TRIAL_THRESHOLD_MATCHED = "IMAGE_TRIAL_THRESHOLD_MATCHED"
    IMAGE_TRIAL_THRESHOLD_NOT_MATCHED = "IMAGE_TRIAL_THRESHOLD_NOT_MATCHED"
    MATTING_FAILED = "MATTING_FAILED"
    MATTING_LOW_CONFIDENCE = "MATTING_LOW_CONFIDENCE"
    SUBJECT_CROPPED = "SUBJECT_CROPPED"
    SHOULDERS_NOT_VISIBLE = "SHOULDERS_NOT_VISIBLE"
    PORTRAIT_GEOMETRY_INVALID = "PORTRAIT_GEOMETRY_INVALID"
    PORTRAIT_SOURCE_LIMITED = "PORTRAIT_SOURCE_LIMITED"
    PORTRAIT_UPSCALE_LIMITED = "PORTRAIT_UPSCALE_LIMITED"
    CARD_NOT_FOUND = "CARD_NOT_FOUND"
    CARD_TOO_SMALL = "CARD_TOO_SMALL"
    CARD_CROPPED = "CARD_CROPPED"
    CARD_DETECTION_UNCERTAIN = "CARD_DETECTION_UNCERTAIN"
    INVALID_VIDEO = "INVALID_VIDEO"
    VIDEO_TOO_LARGE = "VIDEO_TOO_LARGE"
    VIDEO_TOO_LONG = "VIDEO_TOO_LONG"
    VIDEO_DECODE_FAILED = "VIDEO_DECODE_FAILED"
    VIDEO_SCAN_LIMIT_REACHED = "VIDEO_SCAN_LIMIT_REACHED"
    CANDIDATE_LIMIT_REACHED = "CANDIDATE_LIMIT_REACHED"
    THRESHOLD_NOT_CONFIGURED = "THRESHOLD_NOT_CONFIGURED"
    CANDIDATE_LANDMARK_INVALID = "CANDIDATE_LANDMARK_INVALID"
    CANDIDATE_ALIGNMENT_INVALID = "CANDIDATE_ALIGNMENT_INVALID"
    CANDIDATE_FEATURE_INVALID = "CANDIDATE_FEATURE_INVALID"
    CANDIDATE_COMPARISON_INCOMPLETE = "CANDIDATE_COMPARISON_INCOMPLETE"
    INSUFFICIENT_VALID_FRAMES = "INSUFFICIENT_VALID_FRAMES"
    MULTIPLE_SUBJECTS = "MULTIPLE_SUBJECTS"
    SUBJECT_SWITCH = "SUBJECT_SWITCH"
    SUBJECT_SWITCHED = "SUBJECT_SWITCHED"
    CALIBRATION_REQUIRED = "CALIBRATION_REQUIRED"
    IDENTITY_UNCERTAIN = "IDENTITY_UNCERTAIN"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    INFERENCE_ERROR = "INFERENCE_ERROR"
    ALGORITHM_TIMEOUT = "ALGORITHM_TIMEOUT"


_IMAGE_TRIAL_REASON_CODES = frozenset({
    ReasonCode.IMAGE_TRIAL_THRESHOLD_MATCHED,
    ReasonCode.IMAGE_TRIAL_THRESHOLD_NOT_MATCHED,
})


IMAGE_RECAPTURE_REASON_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.INVALID_IMAGE,
        ReasonCode.IMAGE_TOO_LARGE,
        ReasonCode.UNSUPPORTED_FORMAT,
        ReasonCode.NO_FACE,
        ReasonCode.MULTIPLE_FACES,
        ReasonCode.FACE_TOO_SMALL,
        ReasonCode.FACE_CROPPED,
        ReasonCode.TOO_DARK,
        ReasonCode.TOO_BRIGHT,
        ReasonCode.TOO_BLURRY,
        ReasonCode.BAD_POSE,
        ReasonCode.LANDMARK_FAILED,
        ReasonCode.ALIGNMENT_FAILED,
        ReasonCode.MATTING_LOW_CONFIDENCE,
        ReasonCode.SUBJECT_CROPPED,
        ReasonCode.SHOULDERS_NOT_VISIBLE,
        ReasonCode.PORTRAIT_GEOMETRY_INVALID,
    }
)

VIDEO_RECAPTURE_REASON_CODES: frozenset[ReasonCode] = frozenset(
    (*IMAGE_RECAPTURE_REASON_CODES, ReasonCode.INSUFFICIENT_VALID_FRAMES)
)


class FeatureVersion(str, Enum):
    RECOGNIZER_V1_1024 = "recognizer_v1_1024"
    # SCRFD + ArcFace IResNet-50 (w600k_r50); research-licensed weights.
    ARCFACE_R50_512 = "arcface_r50_512"


FEATURE_DIMENSION = 1024
FEATURE_DIMENSIONS = {
    FeatureVersion.RECOGNIZER_V1_1024: 1024,
    FeatureVersion.ARCFACE_R50_512: 512,
}


class _PublicSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


class LandmarkPoint(_PublicSchema):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class FaceBox(_PublicSchema):
    x1: float = Field(allow_inf_nan=False)
    y1: float = Field(allow_inf_nan=False)
    x2: float = Field(allow_inf_nan=False)
    y2: float = Field(allow_inf_nan=False)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_extents(self) -> Self:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("face box must have positive width and height")
        return self

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


class FaceLandmarks(_PublicSchema):
    points: tuple[LandmarkPoint, ...] = Field(min_length=5, max_length=5)


class Pose(_PublicSchema):
    yaw: float = Field(allow_inf_nan=False)
    pitch: float = Field(allow_inf_nan=False)
    roll: float = Field(allow_inf_nan=False)


class QualityMetric(_PublicSchema):
    name: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    passed: bool


class QualityResult(_PublicSchema):
    passed: bool
    metrics: tuple[QualityMetric, ...] = ()
    reason_codes: tuple[ReasonCode, ...] = ()


class CardRegion(_PublicSchema):
    corners: tuple[LandmarkPoint, ...] = Field(min_length=4, max_length=4)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)


class ResultBase(_PublicSchema):
    success: bool
    suggestion: Suggestion
    reason_codes: tuple[ReasonCode, ...]
    model_versions: Mapping[str, str]
    feature_version: FeatureVersion
    config_version: str = Field(min_length=1)
    processing_time_ms: float = Field(ge=0.0, allow_inf_nan=False)
    trace_id: str = Field(min_length=1)

    @field_validator("model_versions", mode="after")
    @classmethod
    def freeze_model_versions(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return MappingProxyType(dict(value))
        return value

    @field_serializer("model_versions")
    def serialize_model_versions(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class FaceAnalysisResult(ResultBase):
    """Privacy-safe output for one-image usability analysis.

    Coordinates always refer to the rotation-corrected image. Embeddings are
    intentionally absent; trusted callers receive them through a separate
    internal dataclass in :mod:`face_algorithm.core.pipeline`.
    """

    original_width: int | None = Field(default=None, gt=0)
    original_height: int | None = Field(default=None, gt=0)
    box: FaceBox | None = None
    landmarks: FaceLandmarks | None = None
    quality: QualityResult | None = None
    pose: Pose | None = None
    coordinate_space: Literal["corrected_image"] = "corrected_image"
    rotation_quarter_turns: int = Field(ge=0, le=3)
    processing_steps: tuple[str, ...] = ()


class ImageCalibrationSummary(_PublicSchema):
    """Bound image threshold metadata; a configured trial is not calibration."""

    status: Literal["UNCALIBRATED", "ACTIVE", "CONFIGURED_UNVALIDATED"]
    calibration_id: str | None = Field(default=None, min_length=1)
    artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_status_metadata(self) -> Self:
        active = self.status == "ACTIVE"
        if active != (self.calibration_id is not None):
            raise ValueError("active calibration requires an identifier")
        if active != (self.artifact_sha256 is not None):
            raise ValueError("active calibration requires an artifact digest")
        return self


class ImageVerificationSide(_PublicSchema):
    """Privacy-safe summary of one fixed-role image analysis."""

    role: Literal["reference", "probe"]
    analyzed: bool
    usable: bool
    reason_codes: tuple[ReasonCode, ...]
    original_width: int | None = Field(default=None, gt=0)
    original_height: int | None = Field(default=None, gt=0)
    box: FaceBox | None = None
    landmarks: FaceLandmarks | None = None
    quality: QualityResult | None = None
    pose: Pose | None = None
    rotation_quarter_turns: int | None = Field(default=None, ge=0, le=3)
    processing_time_ms: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )
    trace_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_analysis_state(self) -> Self:
        if _IMAGE_TRIAL_REASON_CODES.intersection(self.reason_codes) or (
            self.quality is not None
            and _IMAGE_TRIAL_REASON_CODES.intersection(self.quality.reason_codes)
        ):
            raise ValueError("image trial decisions cannot be side analysis reasons")
        details = (
            self.original_width,
            self.original_height,
            self.box,
            self.landmarks,
            self.quality,
            self.pose,
            self.rotation_quarter_turns,
            self.processing_time_ms,
            self.trace_id,
        )
        if not self.analyzed:
            if self.usable or self.reason_codes or any(item is not None for item in details):
                raise ValueError("unanalyzed side cannot contain analysis state")
            return self
        if (
            self.rotation_quarter_turns is None
            or self.processing_time_ms is None
            or self.trace_id is None
        ):
            raise ValueError("analyzed side requires trace and timing metadata")
        if self.usable and self.reason_codes:
            raise ValueError("usable side cannot contain failure reasons")
        if not self.usable and not self.reason_codes:
            raise ValueError("unusable analyzed side requires a structured reason")
        return self


class ImageVerificationResult(ResultBase):
    """Public two-image result; embeddings and image buffers are never fields."""

    reference: ImageVerificationSide
    probe: ImageVerificationSide
    similarity_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    matcher_version: Literal["l2_dot_v1"] = "l2_dot_v1"
    feature_dimension: Literal[1024] = 1024
    decision_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    calibration: ImageCalibrationSummary

    @model_validator(mode="after")
    def validate_verification_invariants(self) -> Self:
        stable_side_reasons = tuple(
            dict.fromkeys((*self.reference.reason_codes, *self.probe.reason_codes))
        )
        if self.reference.role != "reference" or self.probe.role != "probe":
            raise ValueError("verification sides must retain fixed roles")
        if self.similarity_score is not None and not (
            self.reference.usable and self.probe.usable
        ):
            raise ValueError("similarity requires two usable sides")
        if self.calibration.status == "UNCALIBRATED":
            if self.decision_threshold is not None or self.suggestion is Suggestion.PASS:
                raise ValueError("uncalibrated result cannot contain a passing threshold")
        elif self.decision_threshold is None:
            raise ValueError("bound calibration or trial requires its decision threshold")
        if (
            self.calibration.status == "CONFIGURED_UNVALIDATED"
            and self.suggestion is Suggestion.PASS
        ):
            raise ValueError("configured image trial cannot PASS without calibration")
        if _IMAGE_TRIAL_REASON_CODES.intersection(self.reason_codes):
            if (
                self.calibration.status != "CONFIGURED_UNVALIDATED"
                or not self.success
                or self.suggestion is not Suggestion.REVIEW
                or self.similarity_score is None
                or self.decision_threshold is None
                or not self.reference.usable
                or not self.probe.usable
            ):
                raise ValueError("image trial REVIEW requires a usable configured comparison")
            expected = (
                ReasonCode.IMAGE_TRIAL_THRESHOLD_MATCHED
                if self.similarity_score >= self.decision_threshold
                else ReasonCode.IMAGE_TRIAL_THRESHOLD_NOT_MATCHED
            )
            if self.reason_codes != (expected,):
                raise ValueError("image trial reason contradicts its threshold comparison")
            return self
        if self.suggestion is Suggestion.PASS:
            if (
                not self.success
                or self.reason_codes
                or self.similarity_score is None
                or self.decision_threshold is None
                or self.similarity_score < self.decision_threshold
            ):
                raise ValueError("PASS result violates the threshold contract")
        elif self.suggestion is Suggestion.RECAPTURE:
            if (
                not self.success
                or not self.reason_codes
                or self.reason_codes != stable_side_reasons
                or self.similarity_score is not None
                or any(
                    reason not in IMAGE_RECAPTURE_REASON_CODES
                    for reason in self.reason_codes
                )
            ):
                raise ValueError("RECAPTURE result violates the quality contract")
        else:
            if not self.reason_codes:
                raise ValueError("REVIEW result requires a structured reason")
            if self.reason_codes == (ReasonCode.THRESHOLD_UNCALIBRATED,):
                if (
                    not self.success
                    or self.calibration.status != "UNCALIBRATED"
                    or self.similarity_score is None
                    or not self.reference.usable
                    or not self.probe.usable
                ):
                    raise ValueError("uncalibrated REVIEW violates its decision contract")
            elif self.reason_codes == (ReasonCode.IDENTITY_UNCERTAIN,):
                if (
                    not self.success
                    or self.calibration.status != "ACTIVE"
                    or self.similarity_score is None
                    or self.decision_threshold is None
                    or self.similarity_score >= self.decision_threshold
                    or not self.reference.usable
                    or not self.probe.usable
                ):
                    raise ValueError("uncertain REVIEW violates its threshold contract")
            elif (
                self.success
                or self.similarity_score is not None
                or ReasonCode.THRESHOLD_UNCALIBRATED in self.reason_codes
                or ReasonCode.IDENTITY_UNCERTAIN in self.reason_codes
                or not any(
                    reason not in IMAGE_RECAPTURE_REASON_CODES
                    for reason in self.reason_codes
                )
                or self.reason_codes != stable_side_reasons
            ):
                raise ValueError("hard-failure REVIEW violates its failure contract")
        return self


class FrameSummary(_PublicSchema):
    """Aggregate-only accounting for sampled video frames."""

    requested_frames: int = Field(ge=0)
    decoded_frames: int = Field(ge=0)
    analyzed_frames: int = Field(ge=0)
    valid_frames: int = Field(ge=0)
    no_face_frames: int = Field(ge=0)
    multiple_face_frames: int = Field(ge=0)
    bad_quality_frames: int = Field(ge=0)
    failed_frames: int = Field(ge=0)
    first_timestamp_seconds: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )
    last_timestamp_seconds: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )
    reason_codes: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def validate_frame_accounting(self) -> Self:
        if not self.analyzed_frames <= self.decoded_frames <= self.requested_frames:
            raise ValueError("frame counts must satisfy analyzed <= decoded <= requested")
        categorized = (
            self.valid_frames
            + self.no_face_frames
            + self.multiple_face_frames
            + self.bad_quality_frames
            + self.failed_frames
        )
        if categorized != self.analyzed_frames:
            raise ValueError("analyzed frames must have exactly one category")
        timestamps = (self.first_timestamp_seconds, self.last_timestamp_seconds)
        if self.decoded_frames == 0:
            if any(value is not None for value in timestamps):
                raise ValueError("empty frame summary cannot contain timestamps")
        elif any(value is None for value in timestamps):
            raise ValueError("decoded frame summary requires timestamp bounds")
        elif self.last_timestamp_seconds < self.first_timestamp_seconds:
            raise ValueError("last frame timestamp cannot precede the first")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("frame reason codes must be unique and stable")
        return self


class CalibrationSummary(_PublicSchema):
    """Safe signed-calibration metadata for one quick/full video decision."""

    status: Literal["UNCALIBRATED", "ACTIVE"]
    calibration_id: str | None = Field(default=None, min_length=1)
    artifact_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    frame_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    aggregate_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    min_pass_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    decision_rule_version: Literal["video_track_aggregate_v1"] | None = None
    pipeline_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_calibration_state(self) -> Self:
        signed_fields = (
            self.calibration_id,
            self.artifact_sha256,
            self.frame_threshold,
            self.aggregate_threshold,
            self.min_pass_ratio,
            self.decision_rule_version,
        )
        if self.status == "UNCALIBRATED":
            if any(value is not None for value in signed_fields):
                raise ValueError("uncalibrated summary cannot expose signed thresholds")
        elif any(value is None for value in signed_fields):
            raise ValueError("active calibration requires complete signed metadata")
        return self


class VideoVerificationResult(ResultBase):
    """Safe video result with no frame crops, landmarks, sessions or embeddings."""

    mode: Literal["quick", "full"]
    duration_seconds: float | None = Field(
        default=None,
        gt=0.0,
        allow_inf_nan=False,
    )
    frames: FrameSummary
    aggregate_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    median_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    minimum_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    pass_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    primary_track_coverage: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    secondary_track_ratio: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    longest_no_face_gap_seconds: float = Field(ge=0.0, allow_inf_nan=False)
    feature_dimension: Literal[1024] = 1024
    matcher_version: Literal["l2_dot_v1"] = "l2_dot_v1"
    calibration: CalibrationSummary
    liveness_evaluated: Literal[False] = False

    @model_validator(mode="after")
    def validate_video_decision(self) -> Self:
        if _IMAGE_TRIAL_REASON_CODES.intersection(
            (*self.reason_codes, *self.frames.reason_codes)
        ):
            raise ValueError("image trial reasons cannot describe a video decision")
        if self.frames.decoded_frames and self.duration_seconds is None:
            raise ValueError("decoded video result requires a duration")
        score_fields = (
            self.aggregate_score,
            self.median_score,
            self.minimum_score,
        )
        if any(value is None for value in score_fields) and any(
            value is not None for value in score_fields
        ):
            raise ValueError("video score aggregates must be present together")
        if self.pass_ratio is not None and self.aggregate_score is None:
            raise ValueError("pass ratio requires identity score aggregates")
        if self.calibration.status == "UNCALIBRATED":
            if self.suggestion is Suggestion.PASS or self.pass_ratio is not None:
                raise ValueError("uncalibrated video result cannot pass")
        if self.suggestion is Suggestion.PASS:
            if (
                not self.success
                or self.reason_codes
                or self.calibration.status != "ACTIVE"
                or self.aggregate_score is None
                or self.pass_ratio is None
                or self.calibration.aggregate_threshold is None
                or self.calibration.min_pass_ratio is None
                or self.aggregate_score < self.calibration.aggregate_threshold
                or self.pass_ratio < self.calibration.min_pass_ratio
            ):
                raise ValueError("PASS result violates a signed threshold")
        elif self.suggestion is Suggestion.RECAPTURE:
            if (
                not self.success
                or not self.reason_codes
                or any(
                    reason not in VIDEO_RECAPTURE_REASON_CODES
                    for reason in self.reason_codes
                )
            ):
                raise ValueError("RECAPTURE violates the video quality contract")
        elif not self.reason_codes:
            raise ValueError("REVIEW requires a structured reason")
        elif ReasonCode.IDENTITY_UNCERTAIN in self.reason_codes:
            if (
                self.reason_codes != (ReasonCode.IDENTITY_UNCERTAIN,)
                or not self.success
                or self.calibration.status != "ACTIVE"
                or self.aggregate_score is None
                or self.pass_ratio is None
                or self.calibration.aggregate_threshold is None
                or self.calibration.min_pass_ratio is None
                or (
                    self.aggregate_score >= self.calibration.aggregate_threshold
                    and self.pass_ratio >= self.calibration.min_pass_ratio
                )
            ):
                raise ValueError("uncertain REVIEW violates its signed thresholds")
        return self


class ModelState(str, Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class HealthLevel(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ModelHealth(_PublicSchema):
    name: str = Field(min_length=1)
    required: bool
    state: ModelState
    version: str = Field(min_length=1)
    providers: tuple[str, ...]
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    feature_version: FeatureVersion | None = None
    output_dimension: int | None = Field(default=None, gt=0)
    error_code: ReasonCode | None = None
    error_message: str | None = None


class HealthStatus(_PublicSchema):
    status: HealthLevel
    models: tuple[ModelHealth, ...]
