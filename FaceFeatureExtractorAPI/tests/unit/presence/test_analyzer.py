from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from face_algorithm.core.face_selection import FaceCandidateBatch
from face_algorithm.presence.config import VideoPresencePolicy
from face_algorithm.schemas import ReasonCode
from .conftest import Aligner, Embedder, Landmarker, PoseEstimator, box, textured


def analyzer(pipeline, config, **policy):
    from face_algorithm.presence.analyzer import PresenceAnalyzer
    return PresenceAnalyzer(pipeline=pipeline, policy=VideoPresencePolicy(**policy), quality_profiles=config.quality_profiles)


def test_candidate_scoring_keeps_non_largest_faces(pipeline_factory, config):
    analysis = analyzer(pipeline_factory({1: (box(20, 80, 180), box(240, 100, 64))}), config)
    result = analysis.analyze_frame(textured(1))
    assert len(result.features) == 2
    assert [int(np.argmax(vector)) for vector in result.features] == [1, 0]
    assert result.candidate_count == 2
    assert not result.system_failure


def test_reference_requires_unambiguous_primary(pipeline_factory, config):
    analysis = analyzer(pipeline_factory({0: (box(20, 100, 140), box(220, 100, 140))}), config)
    result = analysis.analyze_reference(textured())
    assert result.features == ()
    assert ReasonCode.MULTIPLE_FACES in result.reason_codes
    assert not result.system_failure


def test_reference_allows_small_background_face(pipeline_factory, config):
    result = analyzer(pipeline_factory({0: (box(50, 50, 240), box(320, 280, 40))}), config).analyze_reference(textured())
    assert len(result.features) == 1


@pytest.mark.parametrize("size,valid", [(63, False), (64, True), (112, True)])
def test_video_size_floor_uses_visible_roi(pipeline_factory, config, size, valid):
    result = analyzer(pipeline_factory({1: (box(200, 100, size),)}), config).analyze_frame(textured(1))
    assert bool(result.features) is valid
    if not valid:
        assert ReasonCode.FACE_TOO_SMALL in result.reason_codes


def test_reference_size_floor_is_112(pipeline_factory, config):
    result = analyzer(pipeline_factory({0: (box(100, 100, 111),)}), config).analyze_reference(textured())
    assert not result.features
    assert ReasonCode.FACE_TOO_SMALL in result.reason_codes


def test_bbox_may_touch_edge_but_landmarks_must_be_visible(pipeline_factory, config):
    passing = analyzer(pipeline_factory({1: (box(0, 40, 128),)}), config).analyze_frame(textured(1))
    assert len(passing.features) == 1
    failing = analyzer(pipeline_factory({1: (box(0, 40, 128),)}, landmarker=Landmarker(edge=True)), config).analyze_frame(textured(1))
    assert not failing.features
    assert ReasonCode.FACE_CROPPED in failing.reason_codes or ReasonCode.LANDMARK_FAILED in failing.reason_codes


def test_truncated_bbox_cannot_claim_invisible_resolution(pipeline_factory, config):
    result = analyzer(pipeline_factory({1: (box(-120, 100, 160),)}), config).analyze_frame(textured(1))
    assert not result.features
    assert ReasonCode.FACE_TOO_SMALL in result.reason_codes


def test_missing_landmarks_and_invalid_features_fail_closed(pipeline_factory, config):
    for pipeline in (pipeline_factory(landmarker=Landmarker(missing=True)), pipeline_factory(embedder=Embedder(np.ones(512, dtype=np.float32)))):
        result = analyzer(pipeline, config).analyze_frame(textured())
        assert not result.features
        assert result.system_failure


def test_pose_uses_aligned_256_protocol(pipeline_factory, config):
    pose = PoseEstimator()
    result = analyzer(pipeline_factory(pose=pose), config).analyze_frame(textured())
    assert len(result.features) == 1
    assert pose.inputs[0][0] == (256, 256, 3)
    assert pose.inputs[0][1].width == 256


def test_no_face_blurry_dark_and_bad_pose_do_not_score(pipeline_factory, config):
    assert not analyzer(pipeline_factory({1: ()}), config).analyze_frame(textured(1)).features
    dark = np.zeros((400, 400, 3), dtype=np.uint8)
    assert ReasonCode.TOO_DARK in analyzer(pipeline_factory(), config).analyze_frame(dark).reason_codes
    blurry = np.full((400, 400, 3), 128, dtype=np.uint8)
    assert ReasonCode.TOO_BLURRY in analyzer(pipeline_factory(), config).analyze_frame(blurry).reason_codes
    assert ReasonCode.BAD_POSE in analyzer(pipeline_factory(pose=PoseEstimator(80)), config).analyze_frame(textured()).reason_codes


@pytest.mark.parametrize("saturated,count", [(False, 6), (True, 5)])
def test_candidate_limit_is_reported_not_global_multiface_rejection(pipeline_factory, config, saturated, count):
    boxes = tuple(box(40 + index * 30, 100, 80) for index in range(count))
    batch = FaceCandidateBatch(candidates=boxes, raw_candidate_count=count + int(saturated), saturated=saturated)
    result = analyzer(pipeline_factory({1: batch}), config).analyze_frame(textured(1))
    assert result.candidate_limit_hit
    assert result.candidate_count == 5
    assert len(result.features) == 5
    assert ReasonCode.MULTIPLE_FACES not in result.reason_codes


def test_analyzer_and_owned_features_are_immutable(pipeline_factory, config):
    value = analyzer(pipeline_factory(), config)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        value._pipeline = None
    result = value.analyze_frame(textured())
    assert not result.features[0].flags.writeable
    assert "array" not in repr(result)


def test_fully_filtered_raw_candidates_do_not_imply_unprocessed_faces(pipeline_factory, config):
    batch = FaceCandidateBatch(candidates=(box(),), raw_candidate_count=8, saturated=False)
    result = analyzer(pipeline_factory({1: batch}), config).analyze_frame(textured(1))
    assert len(result.features) == 1
    assert not result.candidate_limit_hit


def test_reference_obeys_configured_candidate_cap(pipeline_factory, config):
    result = analyzer(pipeline_factory({0: (box(50, 50, 240), box(320, 280, 40))}), config,
                      max_face_candidates=1).analyze_reference(textured())
    assert not result.features
    assert result.candidate_limit_hit


def test_failure_in_later_candidate_preserves_completed_work_count(pipeline_factory, config):
    class FailingLandmarker(Landmarker):
        def detect(self, image, face):
            if face.x1 > 200:
                raise RuntimeError("not public")
            return super().detect(image, face)
    result = analyzer(pipeline_factory({1: (box(20, 80, 180), box(240, 100, 64))},
                                       landmarker=FailingLandmarker()), config).analyze_frame(textured(1))
    assert result.candidate_count == 2
    assert result.system_failure


def test_later_alignment_exception_preserves_candidate_accounting(pipeline_factory, config):
    class FailingAligner(Aligner):
        def align(self, image, landmarks):
            if landmarks[2, 0] > 220:
                raise RuntimeError("not public")
            return super().align(image, landmarks)
    pipeline = pipeline_factory({1: (box(20, 80, 180), box(240, 100, 64))}, aligner=FailingAligner())
    result = analyzer(pipeline, config).analyze_frame(textured(1))
    assert result.candidate_count == 2 and result.system_failure
