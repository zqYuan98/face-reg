import pytest


def prediction(pair_id, decision="INCONCLUSIVE", score=0.9):
    return dict(pair_id=pair_id, decision=decision,
                found={"FOUND": True, "NOT_FOUND_IN_SCAN": False, "INCONCLUSIVE": None}[decision],
                best_score=score, success=True, scan_complete=True)


def test_unlabelled_high_scores_never_become_truth():
    from scripts.evaluate_presence_labels import evaluate_records
    report = evaluate_records([prediction("pair-001", "FOUND")], [])
    assert report["unknown"] == 1 and report["labelled"] == 0
    assert report["false_positive_rate"] is None


def test_counts_are_video_level_and_uncertain_is_excluded():
    from scripts.evaluate_presence_labels import evaluate_records
    rows = [prediction("pair-001", "FOUND"), prediction("pair-002", "NOT_FOUND_IN_SCAN"),
            prediction("pair-003"), prediction("pair-004", "FOUND")]
    labels = [dict(pair_id=f"pair-{i:03}", presence=label, identity_group=f"group-{i}",
                   video_id=f"video-{i}",
                   split="evaluation", review_version="human-v1")
              for i, label in enumerate(["absent", "present", "present", "uncertain"], 1)]
    report = evaluate_records(rows, labels)
    assert report["false_positives"] == 1 and report["false_negatives"] == 1
    assert report["uncertain"] == 1 and report["labelled"] == 3
    assert report["labelled_inconclusive"] == 1
    assert report["false_positive_rate"] == 1.0 and report["false_negative_rate"] == 0.5


def test_duplicate_or_cross_split_identity_labels_rejected():
    from scripts.evaluate_presence_labels import evaluate_records
    first = dict(pair_id="pair-001", presence="present", identity_group="group-1",
                 video_id="video-1",
                 split="tuning", review_version="human-v1")
    for labels in ([first, first], [first, first | dict(pair_id="pair-002", split="evaluation")]):
        with pytest.raises(ValueError):
            evaluate_records([prediction("pair-001"), prediction("pair-002")], labels)


def test_malformed_prediction_never_counts_as_negative():
    from scripts.evaluate_presence_labels import evaluate_records
    with pytest.raises(ValueError):
        evaluate_records([prediction("pair-001", "NOT_FOUND_IN_SCAN") | {"found": None}], [])


@pytest.mark.parametrize("video_id", [None, "video-other"])
def test_reviewed_labels_require_explicit_matching_video_id(video_id):
    from scripts.evaluate_presence_labels import evaluate_records
    row = prediction("pair-001") | {"video_id": "video-shared"}
    label = dict(pair_id="pair-001", presence="present", identity_group="group-1",
                 split="tuning", review_version="human-v1", video_id=video_id)
    with pytest.raises(ValueError):
        evaluate_records([row], [label])


def test_video_cannot_cross_splits_even_for_different_reference_identities():
    from scripts.evaluate_presence_labels import evaluate_records
    rows = [prediction(f"pair-{i}") | {"video_id": "video-shared"} for i in (1, 2)]
    labels = [dict(pair_id=f"pair-{i}", presence="present", identity_group=f"group-{i}",
                   video_id="video-shared", split=split, review_version="human-v1")
              for i, split in [(1, "tuning"), (2, "evaluation")]]
    with pytest.raises(ValueError, match="a video appears"):
        evaluate_records(rows, labels)
