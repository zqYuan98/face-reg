import numpy as np
import pytest

from face_algorithm.schemas import Pose, ReasonCode
from .conftest import Landmarker, box, textured


@pytest.mark.parametrize("kind", ["missing", "nan", "inf", "collapsed", "collinear", "outside", "extreme"])
def test_alignment_preflight_rejects_only_unusable_geometry(kind):
    from face_algorithm.presence.quality import alignment_issue

    points = Landmarker().detect(textured(), box())
    if kind == "missing":
        points = points[:4]
    elif kind in {"nan", "inf"}:
        points[0, 0] = np.nan if kind == "nan" else np.inf
    elif kind == "collapsed":
        points[:] = (200, 200)
    elif kind == "collinear":
        points[:] = np.arange(5)[:, None] + 100
    elif kind == "outside":
        points += 10000
    else:
        points *= 1e30
    issue = alignment_issue(points, (400, 400, 3))
    expected = "CANDIDATE_LANDMARK_INVALID" if kind in {"missing", "nan", "inf"} else "CANDIDATE_ALIGNMENT_INVALID"
    assert issue is not None and issue.value == expected


@pytest.mark.parametrize("size", [1, 16, 63, 112])
def test_alignment_preflight_has_no_face_size_floor(size):
    from face_algorithm.presence.quality import alignment_issue

    points = Landmarker().detect(textured(), box(0, 0, size))
    assert alignment_issue(points, (400, 400, 3)) is None


def test_alignment_preflight_accepts_visible_source_with_partial_padding():
    from face_algorithm.presence.quality import alignment_issue

    points = Landmarker(edge=True).detect(textured(), box(0, 0, 128))
    points[0, 0] = -2
    assert alignment_issue(points, (400, 400, 3)) is None


def test_zero_source_pixel_support_is_invalid_even_when_transform_is_finite():
    from face_algorithm.presence.quality import alignment_issue

    points = Landmarker().detect(textured(), box())
    assert alignment_issue(points, (1, 400, 3)).value == "CANDIDATE_ALIGNMENT_INVALID"


def test_nondegenerate_points_with_degenerate_template_fit_are_rejected():
    from face_algorithm.presence.quality import alignment_issue
    from models.facealign import FaceAlign

    template = np.asarray(FaceAlign().mean_face, dtype=np.float64).reshape(5, 2)
    design = np.column_stack((template, np.ones(5)))
    _, _, basis = np.linalg.svd(design.T, full_matrices=True)
    # These two independent point axes have zero similarity-fit covariance.
    points = (basis[-2:].T * 40 + 200).astype(np.float32)
    issue = alignment_issue(points, (400, 400, 3))
    assert issue is not None and issue.value == "CANDIDATE_ALIGNMENT_INVALID"


def test_preflight_padding_decision_matches_production_alignment():
    from face_algorithm.presence.quality import alignment_issue
    from models.facealign import FaceAlign

    source = np.full((400, 400, 3), 128, dtype=np.uint8)
    for offset in (-120, 0, 10000):
        points = Landmarker().detect(source, box()) + offset
        aligned = FaceAlign().align(source, points.tolist())
        issue = alignment_issue(points, source.shape)
        assert (issue is None) == bool(np.any(aligned))


def test_quality_warning_order_is_stable_and_contains_no_local_failure(config):
    from face_algorithm.presence.quality import evaluate_quality_warnings

    image = np.zeros((400, 400, 3), dtype=np.uint8)
    face = box(0, 0, 16)
    points = Landmarker(edge=True).detect(image, face)
    warnings = evaluate_quality_warnings(image, box=face, landmarks=points,
                                        pose=Pose(yaw=80, pitch=0, roll=0),
                                        profile=config.quality_profiles.contract_video_frame)
    assert warnings == (ReasonCode.TOO_DARK, ReasonCode.FACE_TOO_SMALL, ReasonCode.TOO_BLURRY,
                        ReasonCode.BAD_POSE, ReasonCode.FACE_CROPPED)


def test_quality_uses_only_clipped_visible_crop(config):
    from face_algorithm.presence.quality import evaluate_quality_warnings

    image = textured()
    face = box(0, 100, 40)
    warnings = evaluate_quality_warnings(image, box=face, landmarks=Landmarker().detect(image, face),
                                        pose=Pose(yaw=0, pitch=0, roll=0),
                                        profile=config.quality_profiles.contract_video_frame)
    assert ReasonCode.FACE_TOO_SMALL in warnings
