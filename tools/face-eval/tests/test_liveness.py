from faceeval import liveness
from faceeval.data import load_run
from faceeval.liveness import ClipObs, Rule, accept, evaluate

from synthetic import write_run


def test_window_rule_accepts_on_two_of_three():
    rule = Rule(3, 2)
    assert accept([], rule) is None
    assert accept([True], rule) is False
    assert accept([True, False, True], rule) is True
    assert accept([True, False, False, True], rule) is False  # never two live votes inside one window
    assert accept([False, True, True], rule) is True


def test_undecided_bona_fide_counts_as_rejected_and_unjudgeable_frames_are_skipped():
    clips = [
        ClipObs("a", "live", "", [None, None]),          # no face at all
        ClipObs("b", "live", "", [0.9, None, 0.8]),      # two votes, both live
        ClipObs("c", "attack", "print", [0.9, 0.95]),     # attack accepted
        ClipObs("d", "attack", "print", [0.1, 0.2, 0.3]),
    ]
    point = evaluate(clips, "score", 0.5, Rule(3, 2))
    assert (point.bpcer.k, point.bpcer.n) == (1, 2)
    assert point.undecided_bona == 1
    assert (point.apcer_by_type["print"].k, point.apcer_by_type["print"].n) == (1, 2)


def test_status_sweep_ignores_unknown_codes():
    clip = ClipObs("x", "live", "", [{0.5: 1}, {0.5: -3}, {0.5: -1}, {0.5: 1}])
    assert liveness.votes(clip, "status", 0.5) == [True, True]


def test_parity_picks_matching_bpcer_and_compares_each_attack_type(tmp_path):
    run = load_run(write_run(tmp_path / "run", attacks_per_type=8, ref_attack_leak=0.5))
    rule = Rule(3, 2)
    ref = liveness.default_point(run, "ref", 0.5, rule)
    chosen, matched = liveness.choose_matching(liveness.sweep(run, "ours", rule), ref.bpcer.value)
    assert matched
    assert chosen.bpcer.value <= ref.bpcer.value
    verdicts = liveness.compare_types(chosen, ref, min_attacks=5)
    assert set(verdicts) == {"print", "screen"}
    assert all(v == liveness.PASS for v in verdicts.values())
    assert liveness.compare_types(chosen, ref, min_attacks=50) == {"print": "INSUFFICIENT", "screen": "INSUFFICIENT"}


def test_overall_verdict_precedence():
    assert liveness.overall(["PASS", "PASS"]) == "PASS"
    assert liveness.overall(["PASS", "INSUFFICIENT"]) == "INSUFFICIENT"
    assert liveness.overall(["INSUFFICIENT", "WORSE"]) == "WORSE"
    assert liveness.overall([]) == "INSUFFICIENT"
