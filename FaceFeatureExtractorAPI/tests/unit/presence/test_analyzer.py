from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from face_algorithm.core.face_selection import FaceCandidateBatch
from face_algorithm.presence.config import VideoPresencePolicy
from face_algorithm.schemas import FaceBox, ReasonCode
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


@pytest.mark.parametrize("size", [16, 63, 64, 112])
def test_video_size_floor_only_warns(pipeline_factory, config, size):
    result = analyzer(pipeline_factory({1: (box(200, 100, size),)}), config).analyze_frame(textured(1))
    assert len(result.features) == 1
    assert (ReasonCode.FACE_TOO_SMALL in result.quality_warnings) is (size < 64)
    assert not result.reason_codes


def test_reference_size_floor_only_warns(pipeline_factory, config):
    result = analyzer(pipeline_factory({0: (box(100, 100, 111),)}), config).analyze_reference(textured())
    assert len(result.features) == 1
    assert ReasonCode.FACE_TOO_SMALL in result.quality_warnings
    assert not result.reason_codes


def test_bbox_and_alignable_landmarks_may_touch_edge(pipeline_factory, config):
    passing = analyzer(pipeline_factory({1: (box(0, 40, 128),)}), config).analyze_frame(textured(1))
    assert len(passing.features) == 1
    edge = analyzer(pipeline_factory({1: (box(0, 40, 128),)}, landmarker=Landmarker(edge=True)), config).analyze_frame(textured(1))
    assert len(edge.features) == 1
    assert ReasonCode.FACE_CROPPED in edge.quality_warnings
    assert not edge.reason_codes


def test_truncated_bbox_warns_about_visible_resolution(pipeline_factory, config):
    result = analyzer(pipeline_factory({1: (box(-120, 100, 160),)}), config).analyze_frame(textured(1))
    assert len(result.features) == 1
    assert ReasonCode.FACE_TOO_SMALL in result.quality_warnings
    assert not result.reason_codes


def test_missing_landmarks_are_local_failure(pipeline_factory, config):
    result = analyzer(pipeline_factory(landmarker=Landmarker(missing=True)), config).analyze_frame(textured())
    assert not result.features
    assert not result.system_failure
    assert result.failed_candidates == 1
    assert [reason.value for reason in result.reason_codes] == ["CANDIDATE_LANDMARK_INVALID"]


def test_pose_uses_aligned_256_protocol(pipeline_factory, config):
    pose = PoseEstimator()
    result = analyzer(pipeline_factory(pose=pose), config).analyze_frame(textured())
    assert len(result.features) == 1
    assert pose.inputs[0][0] == (256, 256, 3)
    assert pose.inputs[0][1].width == 256


def test_no_face_has_no_candidate_failures(pipeline_factory, config):
    result = analyzer(pipeline_factory({1: ()}), config).analyze_frame(textured(1))
    assert not result.features
    assert result.reason_codes == (ReasonCode.NO_FACE,)
    assert result.candidate_count == result.failed_candidates == 0


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("quality", ["dark", "blurry", "pose"])
def test_quality_warnings_preserve_comparable_features(pipeline_factory, config, reference, quality):
    image = np.zeros((400, 400, 3), dtype=np.uint8) if quality == "dark" else (
        np.full((400, 400, 3), 128, dtype=np.uint8) if quality == "blurry" else textured()
    )
    value = analyzer(pipeline_factory(pose=PoseEstimator(80 if quality == "pose" else 0)), config)
    result = value.analyze_reference(image) if reference else value.analyze_frame(image)
    expected = {"dark": ReasonCode.TOO_DARK, "blurry": ReasonCode.TOO_BLURRY, "pose": ReasonCode.BAD_POSE}[quality]
    assert len(result.features) == 1
    assert expected in result.quality_warnings
    assert result.feature_quality_warnings == (result.quality_warnings,)
    assert result.feature_candidate_indices == (0,)
    assert not result.reason_codes and not result.system_failure


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


@pytest.mark.parametrize("kind", ["missing", "nonfinite", "collapsed", "collinear", "outside"])
def test_invalid_candidate_continues_other_candidates(pipeline_factory, config, kind):
    class BadFirstLandmarker(Landmarker):
        def detect(self, image, face):
            points = super().detect(image, face)
            if face.x1 >= 100:
                return points
            if kind == "missing":
                return points[:4]
            if kind == "nonfinite":
                points[0, 0] = np.nan
            elif kind == "collapsed":
                points[:] = (40, 40)
            elif kind == "collinear":
                points[:] = np.arange(5)[:, None] + 40
            elif kind == "outside":
                points += 10000
            return points

    result = analyzer(pipeline_factory(
        {1: (box(20, 80, 64), box(240, 100, 64))}, landmarker=BadFirstLandmarker()
    ), config).analyze_frame(textured(1))
    assert len(result.features) == 1
    assert result.feature_candidate_indices == (1,)
    assert len(result.feature_quality_warnings) == 1
    assert result.candidate_count == 2 and result.failed_candidates == 1
    assert not result.system_failure
    expected = "CANDIDATE_LANDMARK_INVALID" if kind in {"missing", "nonfinite"} else "CANDIDATE_ALIGNMENT_INVALID"
    assert [reason.value for reason in result.reason_codes] == [expected]


@pytest.mark.parametrize("bad_value", [0.0, np.nan, np.inf])
def test_candidate_numeric_feature_failure_keeps_prior_and_later_features(pipeline_factory, config, bad_value):
    class NumericFailureEmbedder(Embedder):
        def __init__(self):
            self.calls = 0

        def extract(self, aligned):
            self.calls += 1
            return np.full(1024, bad_value if self.calls == 2 else self.calls, dtype=np.float32)

    result = analyzer(pipeline_factory(
        {1: (box(20, 80, 64), box(120, 80, 64), box(240, 100, 64))}, embedder=NumericFailureEmbedder()
    ), config).analyze_frame(textured(1))
    assert len(result.features) == 2
    assert result.feature_candidate_indices == (0, 2)
    assert result.candidate_count == 3 and result.failed_candidates == 1
    assert not result.system_failure
    assert [reason.value for reason in result.reason_codes] == ["CANDIDATE_FEATURE_INVALID"]
    for vector in result.features:
        assert vector.dtype == np.float32 and vector.shape == (1024,)
        assert np.all(np.isfinite(vector)) and np.linalg.norm(vector) == pytest.approx(1)


@pytest.mark.parametrize("invalid", [
    np.ones(512, dtype=np.float32), np.ones((1, 1024), dtype=np.float32),
    np.ones(1024, dtype=np.float64), [1.0] * 1024,
])
def test_feature_protocol_failure_is_global(pipeline_factory, config, invalid):
    result = analyzer(pipeline_factory(embedder=Embedder(invalid)), config).analyze_frame(textured())
    assert not result.features and result.system_failure
    assert result.failed_candidates == 0
    assert result.reason_codes in {(ReasonCode.FEATURE_FAILED,), (ReasonCode.FEATURE_VERSION_MISMATCH,)}


def test_feature_version_failure_is_global(pipeline_factory, config):
    embedder = Embedder()
    embedder.feature_version = "unsupported"
    result = analyzer(pipeline_factory(embedder=embedder), config).analyze_frame(textured())
    assert result.system_failure and result.reason_codes == (ReasonCode.FEATURE_VERSION_MISMATCH,)


@pytest.mark.parametrize("error", [RuntimeError("session failed"), ValueError("must be finite")])
def test_unknown_embedder_error_is_not_reclassified_as_candidate_failure(pipeline_factory, config, error):
    class FailingEmbedder(Embedder):
        def extract(self, aligned):
            raise error

    result = analyzer(pipeline_factory(embedder=FailingEmbedder()), config).analyze_frame(textured())
    assert result.system_failure
    assert result.failed_candidates == 0


@pytest.mark.parametrize("reference", [False, True])
def test_legacy_eye_mouth_and_vertical_ratios_do_not_block_alignment(pipeline_factory, config, reference):
    class ProfileLandmarker(Landmarker):
        def detect(self, image, face):
            return np.array([(130, 130), (131, 180), (130, 200), (135, 215), (136, 230)], dtype=np.float32)

    value = analyzer(pipeline_factory(landmarker=ProfileLandmarker()), config)
    result = value.analyze_reference(textured()) if reference else value.analyze_frame(textured())
    assert len(result.features) == 1 and not result.reason_codes


def test_reference_selected_index_retains_original_candidate_order(pipeline_factory, config):
    result = analyzer(pipeline_factory({0: (box(320, 280, 40), box(50, 50, 240))}), config).analyze_reference(textured())
    assert len(result.features) == 1
    assert result.feature_candidate_indices == (1,)


def test_quality_profiles_are_owned_immutable_copies(pipeline_factory, config):
    before = config.quality_profiles.model_dump()
    value = analyzer(pipeline_factory(), config, reference_min_face_size=30, video_min_face_size=20)
    assert config.quality_profiles.model_dump() == before
    assert value._reference_profile is not config.quality_profiles.contract_reference
    assert value._frame_profile is not config.quality_profiles.contract_video_frame
    with pytest.raises((FrozenInstanceError, AttributeError, ValueError)):
        value._frame_profile.resolution.width = 999


def test_returned_feature_does_not_share_recognizer_memory(pipeline_factory, config):
    source = np.ones(1024, dtype=np.float32)
    result = analyzer(pipeline_factory(embedder=Embedder(source)), config).analyze_frame(textured())
    assert not np.shares_memory(source, result.features[0])
    source[:] = 0
    assert np.linalg.norm(result.features[0]) == pytest.approx(1)


@pytest.mark.parametrize("magnitude", [np.finfo(np.float32).max, np.nextafter(np.float32(0), np.float32(1))])
def test_finite_extreme_nonzero_feature_normalizes_without_overflow(pipeline_factory, config, magnitude):
    result = analyzer(pipeline_factory(embedder=Embedder(np.full(1024, magnitude, dtype=np.float32))), config).analyze_frame(textured())
    assert len(result.features) == 1 and not result.reason_codes
    assert result.features[0].dtype == np.float32
    assert np.linalg.norm(result.features[0]) == pytest.approx(1)


@pytest.mark.parametrize("invalid", [np.zeros((112, 112, 3), dtype=np.uint8), np.zeros((256, 256, 3), dtype=np.float32)])
def test_alignment_output_protocol_error_is_global(pipeline_factory, config, invalid):
    class InvalidOutputAligner(Aligner):
        def align(self, image, landmarks):
            return invalid

    result = analyzer(pipeline_factory(aligner=InvalidOutputAligner()), config).analyze_frame(textured())
    assert not result.features and result.system_failure
    assert result.failed_candidates == 0


def test_empty_visible_candidate_is_local_and_other_candidates_continue(pipeline_factory, config):
    result = analyzer(pipeline_factory({1: (box(500, 500, 50), box())}), config).analyze_frame(textured(1))
    assert result.feature_candidate_indices == (1,)
    assert result.candidate_count == 2 and result.failed_candidates == 1
    assert not result.system_failure
    assert [reason.value for reason in result.reason_codes] == ["CANDIDATE_ALIGNMENT_INVALID"]


@pytest.mark.parametrize("coordinates", [
    (399.6, 100, 410, 160), (100, 399.6, 160, 410),
    (-10, 100, 0.4, 160), (100, -10, 160, 0.4),
    (100.1, 100, 100.4, 160), (100, 100.1, 160, 100.4),
    (399.5, 100, 410, 160),
])
@pytest.mark.parametrize("reference", [False, True])
def test_rounded_empty_landmark_crop_is_local(pipeline_factory, config, coordinates, reference):
    import cv2
    from models.facelandmarks5er import crop_face

    class RealCropLandmarker(Landmarker):
        def detect(self, image, face):
            height, width = image.shape[:2]
            crop, _ = crop_face(image, (face.x1, face.y1, face.x2, face.y2), height, width)
            # Exercise the actual production crop and resize before the model.
            cv2.resize(crop, (112, 112))
            return super().detect(image, face)

    invalid = FaceBox(x1=coordinates[0], y1=coordinates[1], x2=coordinates[2], y2=coordinates[3])
    candidates = (invalid,) if reference else (box(20, 80, 64), invalid, box(240, 100, 64))
    value = analyzer(pipeline_factory({1: candidates}, landmarker=RealCropLandmarker()), config)
    result = value.analyze_reference(textured(1)) if reference else value.analyze_frame(textured(1))
    assert not result.system_failure
    assert result.failed_candidates == 1
    assert result.reason_codes == (ReasonCode.CANDIDATE_ALIGNMENT_INVALID,)
    assert result.candidate_count == len(candidates)
    assert result.feature_candidate_indices == (() if reference else (0, 2))
    assert len(result.features) == (0 if reference else 2)


def test_subpixel_box_with_nonempty_rounded_crop_is_not_rejected(pipeline_factory, config):
    face = FaceBox(x1=100.4, y1=100, x2=100.6, y2=160)
    result = analyzer(pipeline_factory({1: (face,)}), config).analyze_frame(textured(1))
    assert len(result.features) == 1
    assert not result.system_failure and result.failed_candidates == 0
    assert ReasonCode.FACE_TOO_SMALL in result.quality_warnings


def test_unexpected_landmarker_opencv_error_remains_global(pipeline_factory, config):
    import cv2

    class FailingLandmarker(Landmarker):
        def detect(self, image, face):
            raise cv2.error("unexpected model preprocessing failure")

    result = analyzer(pipeline_factory(landmarker=FailingLandmarker()), config).analyze_frame(textured())
    assert result.system_failure and not result.features
    assert result.reason_codes == (ReasonCode.INFERENCE_ERROR,)
    assert result.failed_candidates == 0


def test_dark_pixels_are_not_mistaken_for_alignment_padding(pipeline_factory, config):
    from models.facealign import FaceAlign

    result = analyzer(pipeline_factory(aligner=FaceAlign()), config).analyze_frame(np.zeros((400, 400, 3), dtype=np.uint8))
    assert len(result.features) == 1
    assert ReasonCode.TOO_DARK in result.quality_warnings


def test_deadline_remains_global_and_propagates(pipeline_factory, config):
    from face_algorithm.video.decoder import VideoDeadlineExceeded

    def expired():
        raise VideoDeadlineExceeded(ReasonCode.ALGORITHM_TIMEOUT, "expired")

    with pytest.raises(VideoDeadlineExceeded):
        analyzer(pipeline_factory(), config).analyze_frame(textured(), check_deadline=expired)
