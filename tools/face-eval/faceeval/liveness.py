"""Presentation attack detection metrics (ISO/IEC 30107-3 style) and the liveness parity check.

A clip is one presentation. Within a clip, a backend accepts the presentation as soon as
``required`` of the last ``window`` evaluable frames voted live, which mirrors how the apps decide.
Frames where the engine could not judge (no face, too small, angle, boundary, multiple faces)
are not votes. A clip with no evaluable frame is undecided and counts as not accepted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .data import Run
from .stats import Rate

ALIVE, NOT_ALIVE = 1, 0
PASS, WORSE, INSUFFICIENT = "PASS", "WORSE", "INSUFFICIENT"


@dataclass(frozen=True)
class Rule:
    window: int = 3
    required: int = 2

    def __post_init__(self):
        if not 1 <= self.required <= self.window:
            raise ValueError("need 1 <= required <= window")


@dataclass
class ClipObs:
    clip_id: str
    label: str
    attack_type: str
    # score backends: list of score or None; status backends: list of {threshold: status}
    observations: list = field(default_factory=list)


@dataclass
class OperatingPoint:
    threshold: float
    bpcer: Rate
    apcer_by_type: dict
    undecided_bona: int
    undecided_attack: int

    @property
    def apcer_max(self) -> float:
        values = [rate.value for rate in self.apcer_by_type.values() if rate.n]
        return max(values) if values else math.nan


def backend_kind(run: Run, backend: str) -> str | None:
    if any(row.backend == backend and not math.isnan(row.live_score) for row in run.frames):
        return "score"
    if any(key[0] == backend for key in run.sweep):
        return "status"
    return None


def clips_for(run: Run, backend: str) -> tuple[str | None, list[ClipObs]]:
    kind = backend_kind(run, backend)
    clips: dict[str, ClipObs] = {}
    rows = sorted((r for r in run.frames if r.backend == backend), key=lambda r: (r.clip_id, r.frame_index))
    for row in rows:
        clip = clips.setdefault(row.clip_id, ClipObs(row.clip_id, row.label, row.attack_type))
        if kind == "score":
            clip.observations.append(None if row.face_count == 0 or math.isnan(row.live_score) else row.live_score)
        elif kind == "status":
            clip.observations.append(run.sweep.get((backend, row.clip_id), {}).get(row.frame_index, {}))
    return kind, list(clips.values())


def votes(clip: ClipObs, kind: str, threshold: float) -> list[bool]:
    if kind == "score":
        return [score > threshold for score in clip.observations if score is not None]
    statuses = (obs.get(round(threshold, 4)) for obs in clip.observations)
    return [status == ALIVE for status in statuses if status in (ALIVE, NOT_ALIVE)]


def accept(clip_votes: list[bool], rule: Rule) -> bool | None:
    if not clip_votes:
        return None
    recent: list[bool] = []
    for vote in clip_votes:
        recent.append(vote)
        if len(recent) > rule.window:
            recent.pop(0)
        if sum(recent) >= rule.required:
            return True
    return False


def evaluate(clips: list[ClipObs], kind: str, threshold: float, rule: Rule) -> OperatingPoint:
    bona_n = bona_rejected = undecided_bona = undecided_attack = 0
    attacks: dict[str, list[int]] = {}
    for clip in clips:
        decision = accept(votes(clip, kind, threshold), rule)
        if clip.label == "live":
            bona_n += 1
            bona_rejected += decision is not True
            undecided_bona += decision is None
        else:
            counts = attacks.setdefault(clip.attack_type or "other", [0, 0])
            counts[0] += decision is True
            counts[1] += 1
            undecided_attack += decision is None
    return OperatingPoint(threshold, Rate(bona_rejected, bona_n),
                          {kind_: Rate(k, n) for kind_, (k, n) in sorted(attacks.items())},
                          undecided_bona, undecided_attack)


def frame_level(clips: list[ClipObs], kind: str, threshold: float) -> tuple[Rate, Rate]:
    """Each evaluable frame as an independent decision: (BPCER, APCER over all attack frames)."""
    bona = [0, 0]
    attack = [0, 0]
    for clip in clips:
        for vote in votes(clip, kind, threshold):
            if clip.label == "live":
                bona[0] += not vote
                bona[1] += 1
            else:
                attack[0] += vote
                attack[1] += 1
    return Rate(*bona), Rate(*attack)


def candidate_thresholds(run: Run, backend: str, kind: str, clips: list[ClipObs]) -> list[float]:
    if kind == "score":
        scores = sorted({round(s, 6) for c in clips for s in c.observations if s is not None})
        return [scores[0] - 1e-6] + scores if scores else []
    found = sorted({t for (b, _), frames in run.sweep.items() if b == backend
                    for obs in frames.values() for t in obs})
    return found


def sweep(run: Run, backend: str, rule: Rule) -> list[OperatingPoint]:
    kind, clips = clips_for(run, backend)
    if kind is None:
        return []
    return [evaluate(clips, kind, t, rule) for t in candidate_thresholds(run, backend, kind, clips)]


def default_point(run: Run, backend: str, threshold: float, rule: Rule) -> OperatingPoint | None:
    kind, clips = clips_for(run, backend)
    if kind is None:
        return None
    return evaluate(clips, kind, threshold, rule)


def choose_matching(points: list[OperatingPoint], target_bpcer: float) -> tuple[OperatingPoint | None, bool]:
    """Lowest worst-case APCER among thresholds whose BPCER does not exceed the reference.

    Returns (point, matched). When no threshold reaches the reference BPCER, returns the
    lowest-BPCER point with matched=False.
    """
    usable = [p for p in points if p.bpcer.n]
    if not usable:
        return None, False
    eligible = [p for p in usable if p.bpcer.value <= target_bpcer + 1e-12 and not math.isnan(p.apcer_max)]
    if eligible:
        return min(eligible, key=lambda p: (p.apcer_max, p.bpcer.value)), True
    return min(usable, key=lambda p: (p.bpcer.value, p.apcer_max if not math.isnan(p.apcer_max) else 1.0)), False


def compare_types(ours: OperatingPoint, ref: OperatingPoint, min_attacks: int) -> dict[str, str]:
    verdicts = {}
    for attack_type in sorted(set(ours.apcer_by_type) | set(ref.apcer_by_type)):
        mine, theirs = ours.apcer_by_type.get(attack_type), ref.apcer_by_type.get(attack_type)
        if mine is None or theirs is None or min(mine.n, theirs.n) < min_attacks:
            verdicts[attack_type] = INSUFFICIENT
        elif mine.value <= theirs.value:
            verdicts[attack_type] = PASS
        else:
            verdicts[attack_type] = WORSE
    return verdicts


def overall(verdicts: list[str]) -> str:
    if not verdicts or WORSE in verdicts:
        return WORSE if verdicts else INSUFFICIENT
    if INSUFFICIENT in verdicts:
        return INSUFFICIENT
    return PASS
