"""Offline anonymous video-level evaluation; never infers labels from scores/names."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path

_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_DECISIONS = {"FOUND": True, "NOT_FOUND_IN_SCAN": False, "INCONCLUSIVE": None}


def _anonymous_id(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("expected an anonymous lowercase identifier")
    return value


def evaluate_records(records: list[dict], labels: list[dict]) -> dict:
    """Evaluate independent human labels; abstentions are reported separately."""
    predictions = {}
    for row in records:
        pair_id = _anonymous_id(row.get("pair_id"))
        if pair_id in predictions:
            raise ValueError("duplicate prediction identifier")
        decision = row.get("decision")
        if decision not in _DECISIONS or row.get("found", "missing") is not _DECISIONS[decision]:
            raise ValueError("prediction has inconsistent decision/found")
        score = row.get("best_score")
        if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not -1 <= score <= 1):
            raise ValueError("prediction score is invalid")
        if decision != "INCONCLUSIVE" and (row.get("success") is not True
                or row.get("scan_complete") is not True or score is None):
            raise ValueError("incomplete/failed predictions cannot make a match decision")
        predictions[pair_id] = row
    truth, identity_splits, video_splits = {}, {}, {}
    for row in labels:
        pair_id = _anonymous_id(row.get("pair_id"))
        if pair_id in truth or pair_id not in predictions:
            raise ValueError("duplicate label or label without a prediction")
        presence = row.get("presence")
        if presence not in ("present", "absent", "uncertain", "unknown"):
            raise ValueError("label must be independently reviewed or explicitly unknown")
        if presence != "unknown":
            if row.get("split") not in ("tuning", "evaluation") or not str(row.get("review_version", "")).strip():
                raise ValueError("reviewed labels require split and review_version")
            groups = [row.get("identity_group")]
            groups.extend(group for group in str(row.get("appearing_groups", "")).split(";") if group)
            for group in groups:
                group = _anonymous_id(group)
                if identity_splits.setdefault(group, row["split"]) != row["split"]:
                    raise ValueError("an identity appears in both tuning and evaluation")
            video_id = _anonymous_id(row.get("video_id"))
            predicted_video = predictions[pair_id].get("video_id")
            if predicted_video is not None and _anonymous_id(predicted_video) != video_id:
                raise ValueError("label video_id does not match the prediction")
            if video_splits.setdefault(video_id, row["split"]) != row["split"]:
                raise ValueError("a video appears in both tuning and evaluation")
        truth[pair_id] = row
    counts = Counter()
    for pair_id, prediction in predictions.items():
        presence = truth.get(pair_id, {}).get("presence", "unknown")
        counts[presence] += 1
        if presence not in ("present", "absent"):
            continue
        counts["labelled"] += 1
        decision = prediction["decision"]
        if decision == "INCONCLUSIVE":
            counts["labelled_inconclusive"] += 1
            counts[presence + "_inconclusive"] += 1
        elif presence == "present" and decision == "NOT_FOUND_IN_SCAN":
            counts["false_negatives"] += 1
        elif presence == "absent" and decision == "FOUND":
            counts["false_positives"] += 1
        else:
            counts["correct_decisions"] += 1
    result = {key: counts[key] for key in ("present", "absent", "uncertain", "unknown", "labelled",
              "labelled_inconclusive", "present_inconclusive", "absent_inconclusive",
              "false_positives", "false_negatives", "correct_decisions")}
    result.update(requests=len(records), scored=sum(row.get("best_score") is not None for row in records),
        false_positive_rate=counts["false_positives"] / counts["absent"] if counts["absent"] else None,
        false_negative_rate=counts["false_negatives"] / counts["present"] if counts["present"] else None,
        labelled_decision_coverage=(counts["labelled"] - counts["labelled_inconclusive"]) / counts["labelled"]
            if counts["labelled"] else None,
        note="Video-level reviewed labels only; abstentions are separate, no inferred truth or production accuracy guarantee.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--labels", type=Path, help="Independent anonymous human-label CSV; omit for coverage only")
    parser.add_argument("--split", choices=("tuning", "evaluation"), help="Report one preassigned split only")
    args = parser.parse_args()
    payload = json.loads(args.predictions.read_text(encoding="utf-8"))
    records = payload["records"] if isinstance(payload, dict) else payload
    labels = []
    if args.labels:
        with args.labels.open(encoding="utf-8-sig", newline="") as stream:
            labels = list(csv.DictReader(stream))
    # Check leakage on the full input before selecting the reporting split.
    report = evaluate_records(records, labels)
    if args.split:
        labels = [row for row in labels if row.get("split") == args.split]
        ids = {row["pair_id"] for row in labels}
        report = evaluate_records([row for row in records if row["pair_id"] in ids], labels)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
