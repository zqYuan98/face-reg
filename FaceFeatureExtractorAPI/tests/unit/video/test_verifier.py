from __future__ import annotations

import json
import time
from inspect import signature
from pathlib import Path

import numpy as np
import pytest

from face_algorithm.core.pipeline import TrustedFaceAnalysis
from face_algorithm.runtime.config import load_config
from face_algorithm.runtime.model_registry import VerifiedModelEvidence
from face_algorithm.schemas import (
    FaceAnalysisResult,
    FaceBox,
    FaceLandmarks,
    FeatureVersion,
    LandmarkPoint,
    ReasonCode,
    Suggestion,
    VideoVerificationResult,
)
from face_algorithm.video.decoder import (
    DecodedFrame,
    VideoDeadlineExceeded,
    VideoMetadata,
)
from face_algorithm.video.verifier import (
    _bind_video_verification_runtime,
    build_video_verification_runtime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "algorithm.yaml"
FACE_MODEL_NAMES = (
    "face_detector",
    "face_landmark_primary",
    "face_landmark_secondary",
    "face_recognizer",
    "rotation_classifier",
    "pose_var",
    "pose_conv",
)


def _model_evidence() -> dict[str, VerifiedModelEvidence]:
    return {
        role: VerifiedModelEvidence(
            name=role,
            version="test-v1",
            sha256=f"{index:064x}",
            size=100 + index,
            backend="onnxruntime",
            providers=("CPUExecutionProvider",),
            feature_version=(
                "recognizer_v1_1024" if role == "face_recognizer" else None
            ),
            output_dimension=1024 if role == "face_recognizer" else None,
        )
        for index, role in enumerate(FACE_MODEL_NAMES, start=1)
    }


def _embedding(identity: int = 0, *, dimension: int = 1024) -> np.ndarray:
    value = np.zeros(dimension, dtype=np.float32)
    value[identity] = 1.0
    return value


def _trusted(
    *,
    identity: int = 0,
    reasons: tuple[ReasonCode, ...] = (),
    dimension: int = 1024,
    dx: float = 0.0,
    vector: np.ndarray | None = None,
) -> TrustedFaceAnalysis:
    usable = not reasons
    box = FaceBox(x1=100 + dx, y1=80, x2=220 + dx, y2=240) if usable else None
    landmarks = (
        FaceLandmarks(
            points=tuple(
                LandmarkPoint(x=x + dx, y=y)
                for x, y in (
                    (135, 125),
                    (185, 125),
                    (160, 155),
                    (140, 200),
                    (180, 200),
                )
            )
        )
        if usable
        else None
    )
    return TrustedFaceAnalysis(
        public=FaceAnalysisResult(
            success=True,
            suggestion=Suggestion.PASS if usable else Suggestion.RECAPTURE,
            reason_codes=reasons,
            model_versions={"placeholder": "normalized-by-pipeline"},
            feature_version=FeatureVersion.RECOGNIZER_V1_1024,
            config_version="normalized-by-pipeline",
            processing_time_ms=1.0,
            trace_id="frame-trace",
            original_width=400,
            original_height=400,
            box=box,
            landmarks=landmarks,
            rotation_quarter_turns=0,
        ),
        embedding=(
            vector.copy()
            if usable and vector is not None
            else _embedding(identity, dimension=dimension) if usable else None
        ),
        corrected_image=None,
        aligned_face=None,
    )


class QueuePipeline:
    def __init__(self, outputs, *, config, mutate_inputs: bool = False) -> None:
        self.model_evidence = _model_evidence()
        self.model_versions = {
            role: evidence.version for role, evidence in self.model_evidence.items()
        }
        self.config_version = config.config_version
        self.outputs = list(outputs)
        self.mutate_inputs = mutate_inputs
        self.calls: list[tuple[np.ndarray, object]] = []

    def analyze_trusted(self, image: np.ndarray, profile: object) -> TrustedFaceAnalysis:
        self.calls.append((image, profile))
        if self.mutate_inputs:
            image[:] = 255
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        if not isinstance(output, TrustedFaceAnalysis):
            return output
        return TrustedFaceAnalysis(
            public=output.public.model_copy(
                update={
                    "model_versions": self.model_versions,
                    "config_version": self.config_version,
                }
            ),
            embedding=output.embedding,
            corrected_image=None,
            aligned_face=None,
        )


class FakeStream:
    def __init__(self, metadata: VideoMetadata, frames: tuple[DecodedFrame, ...]) -> None:
        self.metadata = metadata
        self.frames = frames
        self.iterated = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def __iter__(self):
        for frame in self.frames:
            self.iterated += 1
            yield frame


class FakeDecoder:
    def __init__(
        self,
        stream: FakeStream | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.stream = stream
        self.error = error
        self.open_calls = 0

    def open(self, *_args, **_kwargs):
        self.open_calls += 1
        if self.error is not None:
            raise self.error
        assert self.stream is not None
        return self.stream


def _stream(frame_count: int, *, fps: float = 2.0, duration: float | None = None):
    if duration is None:
        duration = frame_count / fps
    frames = tuple(
        DecodedFrame(
            index=index,
            timestamp_seconds=index / fps,
            image=np.full((8, 8, 3), index % 255, dtype=np.uint8),
        )
        for index in range(frame_count)
    )
    return FakeStream(
        VideoMetadata(
            width=8,
            height=8,
            fps=fps,
            total_frames=frame_count,
            duration_seconds=duration,
            container="synthetic",
        ),
        frames,
    )


def _video_file(tmp_path: Path, content: bytes = b"synthetic-video") -> Path:
    path = tmp_path / "video.mp4"
    path.write_bytes(content)
    return path


def _runtime(*, outputs, config, key_store, decoder, mutate_inputs: bool = False):
    pipeline = QueuePipeline(outputs, config=config, mutate_inputs=mutate_inputs)
    runtime = _bind_video_verification_runtime(
        pipeline=pipeline,
        config=config,
        key_store=key_store,
        decoder_factory=lambda: decoder,
    )
    return runtime, pipeline


def test_public_builder_accepts_no_external_pipeline_or_decoder() -> None:
    assert tuple(signature(build_video_verification_runtime).parameters) == (
        "registry",
        "config",
        "key_store",
    )


def test_uncalibrated_similarity_one_never_passes(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    decoder = FakeDecoder(_stream(12))
    runtime, _ = _runtime(
        outputs=(_trusted(), *(_trusted() for _ in range(12))),
        config=config,
        key_store=None,
        decoder=decoder,
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.aggregate_score == pytest.approx(1.0)
    assert result.pass_ratio is None
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (
        ReasonCode.CALIBRATION_REQUIRED,
        ReasonCode.THRESHOLD_UNCALIBRATED,
    )
    assert result.calibration.status == "UNCALIBRATED"
    assert result.calibration.aggregate_threshold is None


def test_active_signed_calibration_can_pass_quick(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    decoder = FakeDecoder(_stream(12))
    runtime, _ = _runtime(
        outputs=(_trusted(), *(_trusted() for _ in range(12))),
        config=bundle.config,
        key_store=bundle.key_store,
        decoder=decoder,
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.suggestion is Suggestion.PASS
    assert result.reason_codes == ()
    assert result.frames.valid_frames == 12
    assert result.pass_ratio == pytest.approx(1.0)
    assert result.calibration.aggregate_threshold == pytest.approx(0.75)
    assert result.calibration.artifact_sha256 is not None


def test_active_low_percentile_is_identity_uncertain_not_recapture(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    borderline = np.zeros(1024, dtype=np.float32)
    borderline[0] = np.float32(0.70)
    borderline[1] = np.float32(np.sqrt(1.0 - 0.70**2))
    outputs = [_trusted()]
    outputs.extend(_trusted(vector=borderline) for _ in range(2))
    outputs.extend(_trusted(identity=0) for _ in range(10))
    runtime, _ = _runtime(
        outputs=outputs,
        config=bundle.config,
        key_store=bundle.key_store,
        decoder=FakeDecoder(_stream(12)),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.suggestion is Suggestion.REVIEW
    assert ReasonCode.IDENTITY_UNCERTAIN in result.reason_codes
    assert ReasonCode.INSUFFICIENT_VALID_FRAMES not in result.reason_codes


def test_bad_pose_with_too_few_valid_frames_requests_recapture(
    tmp_path: Path,
) -> None:
    config = load_config(DEFAULT_CONFIG)
    runtime, _ = _runtime(
        outputs=(_trusted(), *(_trusted(reasons=(ReasonCode.BAD_POSE,)) for _ in range(12))),
        config=config,
        key_store=None,
        decoder=FakeDecoder(_stream(12)),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.suggestion is Suggestion.RECAPTURE
    assert result.reason_codes == (
        ReasonCode.INSUFFICIENT_VALID_FRAMES,
        ReasonCode.BAD_POSE,
    )
    assert ReasonCode.IDENTITY_UNCERTAIN not in result.reason_codes


def test_multiple_faces_maps_to_multiple_subjects_and_review(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    outputs = [_trusted(), _trusted(reasons=(ReasonCode.MULTIPLE_FACES,))]
    outputs.extend(_trusted() for _ in range(11))
    runtime, _ = _runtime(
        outputs=outputs,
        config=bundle.config,
        key_store=bundle.key_store,
        decoder=FakeDecoder(_stream(12)),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.MULTIPLE_SUBJECTS,)
    assert result.frames.multiple_face_frames == 1


def test_sequential_single_face_replacement_is_detected_as_a_switch(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    outputs = [_trusted()]
    outputs.extend(_trusted(identity=0) for _ in range(6))
    outputs.extend(_trusted(identity=1) for _ in range(6))
    runtime, _ = _runtime(
        outputs=outputs,
        config=bundle.config,
        key_store=bundle.key_store,
        decoder=FakeDecoder(_stream(12)),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.SUBJECT_SWITCHED,)
    assert result.secondary_track_ratio == pytest.approx(0.5)


def test_full_long_no_face_gap_is_recapture_not_identity_uncertainty(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    outputs = [_trusted()]
    outputs.extend(_trusted() for _ in range(10))
    outputs.extend(_trusted(reasons=(ReasonCode.NO_FACE,)) for _ in range(11))
    outputs.extend(_trusted() for _ in range(10))
    runtime, _ = _runtime(
        outputs=outputs,
        config=bundle.config,
        key_store=bundle.key_store,
        decoder=FakeDecoder(_stream(31)),
    )

    result = runtime.full_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.mode == "full"
    assert result.suggestion is Suggestion.RECAPTURE
    assert ReasonCode.NO_FACE in result.reason_codes
    assert result.longest_no_face_gap_seconds > 5.0
    assert ReasonCode.IDENTITY_UNCERTAIN not in result.reason_codes


def test_full_tolerates_a_brief_missed_face_when_valid_coverage_is_sufficient(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    outputs = [_trusted()]
    outputs.extend(_trusted() for _ in range(10))
    outputs.append(_trusted(reasons=(ReasonCode.NO_FACE,)))
    outputs.extend(_trusted() for _ in range(10))
    runtime, _ = _runtime(
        outputs=outputs,
        config=bundle.config,
        key_store=bundle.key_store,
        decoder=FakeDecoder(_stream(21)),
    )

    result = runtime.full_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.suggestion is Suggestion.PASS
    assert result.longest_no_face_gap_seconds == pytest.approx(0.5)
    assert result.frames.no_face_frames == 1


def test_decoder_early_end_is_not_accepted_as_a_short_video(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    truncated = _stream(5)
    truncated.metadata = VideoMetadata(
        width=8,
        height=8,
        fps=2.0,
        total_frames=12,
        duration_seconds=6.0,
        container="synthetic",
    )
    runtime, _ = _runtime(
        outputs=(_trusted(), *(_trusted() for _ in range(5))),
        config=config,
        key_store=None,
        decoder=FakeDecoder(truncated),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.success is False
    assert result.reason_codes == (ReasonCode.VIDEO_DECODE_FAILED,)


def test_signed_threshold_snapshot_resists_post_build_object_mutation(
    tmp_path: Path,
    active_video_calibration_bundle,
) -> None:
    bundle = active_video_calibration_bundle
    runtime, _ = _runtime(
        outputs=(_trusted(), *(_trusted() for _ in range(12))),
        config=bundle.config,
        key_store=bundle.key_store,
        decoder=FakeDecoder(_stream(12)),
    )
    state = runtime._verifier._calibration
    assert state.video_thresholds is not None
    assert state.artifact is not None
    object.__setattr__(state.video_thresholds, "quick_aggregate_threshold", 0.0)
    object.__setattr__(state.artifact, "quick_aggregate_threshold", 0.0)

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)


def test_video_byte_limit_is_checked_before_injected_decoder(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    config = config.model_copy(
        update={
            "resources": config.resources.model_copy(update={"max_video_bytes": 10})
        }
    )
    decoder = FakeDecoder(_stream(1))
    runtime, _ = _runtime(
        outputs=(_trusted(),),
        config=config,
        key_store=None,
        decoder=decoder,
    )

    result = runtime.quick_verify(
        np.zeros((8, 8, 3), np.uint8),
        _video_file(tmp_path, b"x" * 11),
    )

    assert result.success is False
    assert result.reason_codes == (ReasonCode.VIDEO_TOO_LARGE,)
    assert decoder.open_calls == 0


def test_duration_limit_is_checked_before_any_frame_is_iterated(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    stream = _stream(1, duration=601.0)
    runtime, _ = _runtime(
        outputs=(_trusted(),),
        config=config,
        key_store=None,
        decoder=FakeDecoder(stream),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.success is False
    assert result.reason_codes == (ReasonCode.VIDEO_TOO_LONG,)
    assert stream.iterated == 0


def test_frame_rate_limit_is_checked_before_any_frame_is_iterated(
    tmp_path: Path,
) -> None:
    config = load_config(DEFAULT_CONFIG)
    stream = _stream(12, fps=config.resources.max_video_fps + 0.01)
    runtime, _ = _runtime(
        outputs=(_trusted(),),
        config=config,
        key_store=None,
        decoder=FakeDecoder(stream),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.success is False
    assert result.reason_codes == (ReasonCode.VIDEO_TOO_LARGE,)
    assert stream.iterated == 0


def test_frame_rate_at_limit_reaches_frame_analysis(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    stream = _stream(12, fps=config.resources.max_video_fps)
    runtime, _ = _runtime(
        outputs=(_trusted(), *(_trusted() for _ in range(12))),
        config=config,
        key_store=None,
        decoder=FakeDecoder(stream),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.frames.analyzed_frames == 12


def test_frame_pixel_limit_is_checked_before_any_frame_is_iterated(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    stream = _stream(1)
    stream.metadata = VideoMetadata(
        width=config.resources.max_image_pixels + 1,
        height=1,
        fps=1.0,
        total_frames=1,
        duration_seconds=1.0,
        container="synthetic",
    )
    runtime, _ = _runtime(
        outputs=(_trusted(),),
        config=config,
        key_store=None,
        decoder=FakeDecoder(stream),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.success is False
    assert result.reason_codes == (ReasonCode.VIDEO_TOO_LARGE,)
    assert stream.iterated == 0


def test_decoder_deadline_is_a_reviewable_internal_failure(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    decoder = FakeDecoder(
        error=VideoDeadlineExceeded(
            ReasonCode.ALGORITHM_TIMEOUT,
            "deadline",
        )
    )
    runtime, _ = _runtime(
        outputs=(_trusted(),),
        config=config,
        key_store=None,
        decoder=decoder,
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))

    assert result.success is False
    assert result.suggestion is Suggestion.REVIEW
    assert result.reason_codes == (ReasonCode.ALGORITHM_TIMEOUT,)


def test_reference_and_decoded_frame_buffers_are_not_mutated(tmp_path: Path) -> None:
    config = load_config(DEFAULT_CONFIG)
    stream = _stream(12)
    reference = np.zeros((8, 8, 3), dtype=np.uint8)
    reference_before = reference.copy()
    frames_before = tuple(frame.image.copy() for frame in stream.frames)
    runtime, _ = _runtime(
        outputs=(_trusted(), *(_trusted() for _ in range(12))),
        config=config,
        key_store=None,
        decoder=FakeDecoder(stream),
        mutate_inputs=True,
    )

    runtime.quick_verify(reference, _video_file(tmp_path))

    np.testing.assert_array_equal(reference, reference_before)
    for frame, before in zip(stream.frames, frames_before):
        np.testing.assert_array_equal(frame.image, before)


def test_wrong_embedding_dimension_fails_closed_without_leaking_vector(
    tmp_path: Path,
) -> None:
    config = load_config(DEFAULT_CONFIG)
    runtime, _ = _runtime(
        outputs=(_trusted(), _trusted(dimension=512), *(_trusted() for _ in range(11))),
        config=config,
        key_store=None,
        decoder=FakeDecoder(_stream(12)),
    )

    result = runtime.quick_verify(np.zeros((8, 8, 3), np.uint8), _video_file(tmp_path))
    encoded = json.dumps(result.model_dump(mode="json"), sort_keys=True)

    assert result.suggestion is Suggestion.REVIEW
    assert ReasonCode.FEATURE_VERSION_MISMATCH in result.reason_codes
    assert "embedding" not in encoded.casefold()
    assert "reference_image" not in encoded.casefold()
    assert isinstance(result, VideoVerificationResult)
