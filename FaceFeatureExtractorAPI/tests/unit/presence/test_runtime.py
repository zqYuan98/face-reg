import inspect
import json
import time
from dataclasses import FrozenInstanceError, dataclass, replace

import numpy as np
import pytest

from face_algorithm.core.face_selection import FaceCandidateBatch
from face_algorithm.presence.config import VideoPresencePolicy
from face_algorithm.schemas import ReasonCode, Suggestion
from face_algorithm.video.decoder import DecodedFrame, VideoDeadlineExceeded, VideoDecodeError, VideoMetadata
from .conftest import Embedder, box, textured


@dataclass(frozen=True)
class Summary:
    decoded_frames: int = 0
    sampled_frames: int = 0
    scan_complete: bool = False
    duration_seconds: float = 0.0
    timestamp_source: str = "unknown"


class Stream:
    def __init__(self, frames, *, error=None, complete=True):
        self.frames = frames
        self.error = error
        self.complete = complete
        self.metadata = VideoMetadata(width=400, height=400, fps=10.0,
            total_frames=max(1, len(frames) * 5), duration_seconds=max(0.1, len(frames) / 2), container="mp4")
        self.summary = Summary()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def __iter__(self):
        for count, frame in enumerate(self.frames, 1):
            self.summary = Summary(frame.index + 1, count, False, frame.timestamp_seconds + 0.1, "pts")
            yield frame
        if self.error:
            raise self.error
        self.summary = Summary(self.summary.decoded_frames, self.summary.sampled_frames,
                               self.complete, self.summary.duration_seconds, self.summary.timestamp_source)


class Decoder:
    def __init__(self, stream):
        self.stream = stream
        self.requests = []

    def open(self, path, **kwargs):
        self.requests.append(kwargs)
        return self.stream


def frames(markers):
    return [DecodedFrame(index=index * 5, timestamp_seconds=index * 0.5, image=textured(marker))
            for index, marker in enumerate(markers)]


def bind(pipeline, config, stream, **policy):
    from face_algorithm.presence.runtime import _bind_video_presence_runtime
    decoder = Decoder(stream)
    if policy:
        config = config.model_copy(update={"video_presence": VideoPresencePolicy(**policy)})
    return _bind_video_presence_runtime(pipeline=pipeline, config=config, decoder_factory=lambda: decoder), decoder


def run(runtime):
    return runtime.verify(textured(), "unused-test.mp4", deadline_monotonic=time.monotonic() + 30)


def test_brief_appearance_among_no_face_frames_is_retained(pipeline_factory, config):
    stream = Stream(frames([2, 2, 0, 2, 2]))
    runtime, decoder = bind(pipeline_factory({2: ()}), config, stream)
    result = run(runtime)
    assert result.success and result.scan_complete
    assert result.best_score == 1.0 and result.best_timestamp_seconds == 1.0
    assert result.valid_face_frames == 1 and result.sampled_frames == 5
    assert result.decoded_frames == 21
    assert result.decision.value == "INCONCLUSIVE" and result.found is None
    assert result.suggestion is Suggestion.REVIEW
    assert stream.closed
    assert decoder.requests[0]["request"].sample_interval_seconds == 0.5
    assert decoder.requests[0]["limits"].max_analyzed_frames == 1200


def test_multiface_target_not_largest_is_scored(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory({1: (box(20, 80, 180), box(240, 100, 64))}), config, Stream(frames([1])))
    result = run(runtime)
    assert result.best_score == 1.0
    assert result.candidate_count == 2 and result.valid_face_frames == 1
    assert len(result.evidence_frames) == 1
    assert ReasonCode.MULTIPLE_FACES not in result.reason_codes


def test_reference_ambiguity_requests_recapture_without_decoding(pipeline_factory, config):
    runtime, decoder = bind(pipeline_factory({0: (box(20, 100, 140), box(220, 100, 140))}), config, Stream(frames([1])))
    result = run(runtime)
    assert result.success and result.suggestion is Suggestion.RECAPTURE
    assert not result.scan_complete and result.sampled_frames == 0
    assert result.found is None and not decoder.requests


def test_evidence_is_top_five_unique_frames_not_candidate_copies(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory({1: (box(20, 80, 180), box(240, 100, 64))}), config, Stream(frames([1] * 8)))
    result = run(runtime)
    assert len(result.evidence_frames) == 5
    assert [frame.frame_index for frame in result.evidence_frames] == [0, 5, 10, 15, 20]
    assert result.valid_face_frames == 8 and result.candidate_count == 16
    payload = json.loads(result.model_dump_json())
    assert all(set(frame) == {"frame_index", "timestamp_seconds", "score", "candidate_index", "quality_warnings"}
               for frame in payload["evidence_frames"])
    assert "embedding" not in json.dumps(payload).lower()


def test_no_face_is_not_a_negative_identity_claim(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory({2: ()}), config, Stream(frames([2, 2])))
    result = run(runtime)
    assert result.success and result.scan_complete
    assert result.best_score is None and result.found is None
    assert result.valid_face_frames == 0
    assert ReasonCode.NO_FACE in result.reason_codes


def test_incomplete_scan_reports_budget_without_claiming_completion(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(), config, Stream(frames([0, 0]), complete=False))
    result = run(runtime)
    assert result.success and not result.scan_complete
    assert ReasonCode.VIDEO_SCAN_LIMIT_REACHED in result.reason_codes


def test_runtime_enforces_own_frame_budget(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(), config, Stream(frames([0] * 5)), max_analyzed_frames=2)
    result = run(runtime)
    assert result.sampled_frames == 2
    assert not result.scan_complete
    assert ReasonCode.VIDEO_SCAN_LIMIT_REACHED in result.reason_codes


def test_candidate_saturation_is_disclosed(pipeline_factory, config):
    batch = FaceCandidateBatch(candidates=(box(),), raw_candidate_count=6, saturated=True)
    runtime, _ = bind(pipeline_factory({1: batch}), config, Stream(frames([1])))
    result = run(runtime)
    assert result.candidate_limit_hit
    assert ReasonCode.CANDIDATE_LIMIT_REACHED in result.reason_codes
    assert result.candidate_count == 1


def test_decoder_error_preserves_partial_observations_without_claiming_complete(pipeline_factory, config):
    stream = Stream(frames([0]), error=VideoDecodeError(ReasonCode.VIDEO_DECODE_FAILED, "unsafe details"))
    runtime, _ = bind(pipeline_factory(), config, stream)
    result = run(runtime)
    assert not result.success and not result.scan_complete
    assert result.best_score == 1.0 and result.sampled_frames == 1
    assert ReasonCode.VIDEO_DECODE_FAILED in result.reason_codes
    assert "unsafe details" not in result.model_dump_json()
    assert stream.closed


def test_expired_deadline_is_raised_before_inference_or_decode(pipeline_factory, config):
    runtime, decoder = bind(pipeline_factory(), config, Stream(frames([0])))
    with pytest.raises(VideoDeadlineExceeded):
        runtime.verify(textured(), "unused", deadline_monotonic=time.monotonic() - 1)
    assert not decoder.requests


def test_decoder_deadline_is_propagated(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(), config, Stream([], error=VideoDeadlineExceeded(ReasonCode.ALGORITHM_TIMEOUT, "late")))
    with pytest.raises(VideoDeadlineExceeded):
        run(runtime)


def test_feature_failure_and_forged_frame_fail_closed(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(embedder=Embedder(np.ones(512, dtype=np.float32))), config, Stream(frames([0])))
    assert not run(runtime).success
    malformed = frames([0])
    malformed[0] = DecodedFrame(index=-1, timestamp_seconds=0.0, image=textured())
    runtime, _ = bind(pipeline_factory(), config, Stream(malformed))
    result = run(runtime)
    assert not result.success and not result.scan_complete


def test_runtime_builder_is_immutable_and_has_no_caller_decoder_injection(pipeline_factory, config):
    from face_algorithm.presence.runtime import VideoPresenceRuntime, build_video_presence_runtime
    assert set(inspect.signature(build_video_presence_runtime).parameters) == {"config", "registry"}
    with pytest.raises(TypeError):
        VideoPresenceRuntime()
    runtime, _ = bind(pipeline_factory(), config, Stream(frames([0])))
    with pytest.raises((FrozenInstanceError, AttributeError)):
        runtime.config_version = "tampered"
    with pytest.raises(TypeError):
        runtime.model_versions["face_recognizer"] = "tampered"


def test_bind_rejects_unverified_model_evidence(pipeline_factory, config):
    pipeline = pipeline_factory()
    object.__setattr__(pipeline, "model_versions", {})
    with pytest.raises(ValueError):
        bind(pipeline, config, Stream(frames([0])))


@pytest.mark.parametrize("changes", [
    {"sampled_frames": 2, "decoded_frames": 2},
    {"decoded_frames": 999999999}, {"duration_seconds": 99999999.0},
    {"timestamp_source": "unknown"},
])
def test_invalid_completion_summary_is_a_system_failure(pipeline_factory, config, changes):
    class ForgedSummary(Stream):
        def __iter__(self):
            yield from super().__iter__()
            self.summary = replace(self.summary, **changes)
    runtime, _ = bind(pipeline_factory(), config, ForgedSummary(frames([0])))
    result = run(runtime)
    assert not result.success and not result.scan_complete
    assert ReasonCode.VIDEO_DECODE_FAILED in result.reason_codes


def test_skipped_initial_frame_cannot_claim_scan_complete(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(), config,
                      Stream([DecodedFrame(index=5, timestamp_seconds=0.5, image=textured())]))
    result = run(runtime)
    assert not result.success and not result.scan_complete


def test_legacy_contract_calibration_is_never_loaded_or_reused(pipeline_factory, config, tmp_path):
    from face_algorithm.runtime.config import AlgorithmConfig
    payload = config.model_dump()
    payload["video_verification"]["calibration"] = dict(
        mode="artifact", artifact_path=tmp_path / "does-not-exist.json",
        expected_sha256="a" * 64, signature_path=tmp_path / "does-not-exist.sig",
        signer_key_id="unrelated-contract-video-key",
    )
    independent = AlgorithmConfig.model_validate(payload)
    runtime, _ = bind(pipeline_factory(), independent, Stream(frames([0])))
    result = run(runtime)
    assert result.best_score == 1.0
    assert result.calibration_status == "UNCALIBRATED"
    assert result.suggestion is Suggestion.REVIEW


def test_policy_quality_and_audit_snapshots_do_not_follow_caller_mutations(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(), config, Stream(frames([0, 0])))
    object.__setattr__(config.video_presence, "max_analyzed_frames", 1)
    object.__setattr__(config.quality_profiles.contract_video_frame.resolution, "width", 1000)
    object.__setattr__(config, "config_version", "tampered")
    result = run(runtime)
    assert result.sampled_frames == 2 and result.valid_face_frames == 2
    assert result.config_version != "tampered"


def test_public_builder_binds_owned_pipeline_and_real_sequential_decoder(pipeline_factory, config, monkeypatch, tmp_path):
    import cv2
    from face_algorithm.core import adapters
    from face_algorithm.presence.runtime import build_video_presence_runtime
    from face_algorithm.video.presence_decoder import OpenCvPresenceDecoder

    calls = []
    registry = object()
    def owned_pipeline(**kwargs):
        calls.append(kwargs)
        return pipeline_factory()
    monkeypatch.setattr(adapters, "build_owned_face_pipeline", owned_pipeline)
    runtime = build_video_presence_runtime(config=config, registry=registry)
    assert calls == [{"config": config, "registry": registry}]
    assert runtime._verifier.decoder_factory is OpenCvPresenceDecoder
    assert not hasattr(runtime, "pipeline")

    path = tmp_path / "synthetic-presence.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (400, 400))
    assert writer.isOpened()
    try:
        for index in range(11):
            writer.write(textured(index))
    finally:
        writer.release()
    result = runtime.verify(textured(), path, deadline_monotonic=time.monotonic() + 30)
    assert result.success and result.scan_complete
    assert result.decoded_frames == 11 and result.sampled_frames == 3
    assert result.timestamp_source in ("pts", "estimated", "mixed")
    assert result.valid_face_frames == 3
    assert result.found is None and result.suggestion is Suggestion.REVIEW


@pytest.mark.parametrize("marker,expected", [(0, "FOUND"), (1, "NOT_FOUND_IN_SCAN"), (2, "INCONCLUSIVE")])
def test_configured_runtime_applies_trial_threshold(pipeline_factory, config, marker, expected):
    runtime, _ = bind(pipeline_factory({2: ()}), config, Stream(frames([marker])),
                      mode="configured", threshold=0.8, threshold_id="synthetic-only")
    result = run(runtime)
    assert result.decision.value == expected
    assert result.calibration_status == "CONFIGURED_UNVALIDATED"
    assert len(result.policy_fingerprint) == 64
    assert result.base_frames == result.sampled_frames == 1 and result.supplemental_frames == 0
    assert ReasonCode.CALIBRATION_REQUIRED not in result.reason_codes


def test_no_threshold_still_compares_without_signature_reason(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(), config, Stream(frames([0])))
    result = run(runtime)
    assert result.comparison_status == "COMPARED"
    assert result.compared_candidates == 1
    assert result.threshold is None
    assert ReasonCode.THRESHOLD_NOT_CONFIGURED in result.reason_codes
    assert ReasonCode.CALIBRATION_REQUIRED not in result.reason_codes


def test_low_quality_is_warning_not_rejection(pipeline_factory, config):
    from .conftest import PoseEstimator
    runtime, _ = bind(pipeline_factory(pose=PoseEstimator(80)), config, Stream(frames([0])),
                      mode="configured", threshold=0.8, threshold_id="synthetic-only")
    result = run(runtime)
    assert result.found is True
    assert ReasonCode.BAD_POSE in result.quality_warnings
    assert ReasonCode.BAD_POSE not in result.reason_codes


@pytest.mark.parametrize("poor,analyzed,extra", [(False, 2, 0), (True, 4, 2)])
def test_window_supplements_are_compared_only_when_base_needs_more_evidence(pipeline_factory, config, poor, analyzed, extra):
    from face_algorithm.presence.sampling import PresenceSampleFrame
    from .conftest import PoseEstimator
    samples = [PresenceSampleFrame(index=i, timestamp_seconds=t, image=textured(), window_index=w, kind=k)
               for i, t, w, k in [(0, 0.0, 0, "base"), (2, 0.2, 0, "supplemental"),
                                 (4, 0.4, 0, "supplemental"), (5, 0.5, 1, "base")]]
    runtime, _ = bind(pipeline_factory(pose=PoseEstimator(80 if poor else 0)), config, Stream(samples),
                      mode="configured", threshold=0.8, threshold_id="synthetic-only")
    result = run(runtime)
    assert result.success and result.scan_complete and result.found is True
    assert result.base_frames == 2 and result.supplemental_frames == extra
    assert result.sampled_frames == analyzed and result.compared_candidates == analyzed
    assert result.decoded_frames == 6


def test_incomplete_scan_never_makes_positive_or_negative_trial_claim(pipeline_factory, config):
    runtime, _ = bind(pipeline_factory(), config, Stream(frames([0]), complete=False),
                      mode="configured", threshold=0.8, threshold_id="synthetic-only")
    result = run(runtime)
    assert result.best_score == 1.0 and result.found is None


def test_policy_fingerprint_changes_with_trial_threshold(pipeline_factory, config):
    a, _ = bind(pipeline_factory(), config, Stream(frames([0])),
                mode="configured", threshold=0.7, threshold_id="synthetic-a")
    b, _ = bind(pipeline_factory(), config, Stream(frames([0])),
                mode="configured", threshold=0.8, threshold_id="synthetic-b")
    assert run(a).policy_fingerprint != run(b).policy_fingerprint


def test_unanalyzed_supplemental_tail_cannot_claim_complete(pipeline_factory, config):
    from face_algorithm.presence.sampling import PresenceSampleFrame
    samples = [PresenceSampleFrame(index=i, timestamp_seconds=t, image=textured(1),
                window_index=0, kind=k) for i, t, k in
               [(0, 0.0, "base"), (2, 0.2, "supplemental")]]
    runtime, _ = bind(pipeline_factory(), config, Stream(samples),
                      mode="configured", threshold=0.8, threshold_id="synthetic-only")
    result = run(runtime)
    assert result.success and not result.scan_complete
    assert result.sampled_frames == 1 and result.best_score is not None
    assert result.found is None
    assert ReasonCode.VIDEO_SCAN_LIMIT_REACHED in result.reason_codes
