"""Session-level comparison from a face-bench run: what a user feels, not every-model-every-frame.

Ours: SessionBackend rows (FaceSession replay with KEEP_ONLY_LATEST drops). detect_ms is the whole
onFrame cost; the recognised frame carries note "recognized:<id>:<score>:<ms since clip start>".

ArcSoft: simulated from its per-frame rows the way its demo app runs: tracking (preprocess +
detect) every processed frame; liveness on each processed frame until 2 of the last 3 say ALIVE;
then a feature on the next frame that has one (the bench extracts every 3rd frame) and accept if
the score against the clip's subject is >= 0.8 (ArcSoft's LIFE_PHOTO recommendation). Frames that
arrive while it is busy are dropped, with the same rule as ours.

    python session_compare.py <run dir> [--arcsoft-threshold 0.8]
"""
import argparse, csv, statistics
from collections import defaultdict
from pathlib import Path


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def pct(values, q):
    values = sorted(v for v in values if v == v)
    if not values:
        return float("nan")
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--arcsoft-threshold", type=float, default=0.8)
    ap.add_argument("--arcsoft-run", help="take ArcSoft rows from this run instead (same clips)")
    a = ap.parse_args()
    run = Path(a.run)
    frames = defaultdict(list)
    scores = {}
    sources = [(run, lambda b: not a.arcsoft_run or b != "arcsoft")]
    if a.arcsoft_run:
        sources.append((Path(a.arcsoft_run), lambda b: b == "arcsoft"))
    for directory, keep in sources:
        for r in csv.DictReader(open(directory / "frames.csv", encoding="utf-8")):
            if keep(r["backend"]):
                frames[(r["backend"], r["clip_id"])].append(r)
        for r in csv.DictReader(open(directory / "scores.csv", encoding="utf-8")):
            if keep(r["backend"]) and r["enrolled_id"] == r["subject_id"]:
                scores[(r["backend"], r["clip_id"], int(r["frame_index"]))] = f(r["score"])
    backends = sorted({b for b, _ in frames})
    clips = sorted({c for _, c in frames})
    label = {c: rows[0]["label"] + ("/" + rows[0]["attack_type"] if rows[0]["attack_type"] else "")
             for (_, c), rows in frames.items()}

    def session(backend):
        out = {}
        for clip in clips:
            rows = sorted(frames[(backend, clip)], key=lambda r: int(r["frame_index"]))
            done = [r for r in rows if r["note"] != "dropped"]
            costs = [f(r["detect_ms"]) for r in done]
            rec = next((r["note"].split(":") for r in done if r["note"].startswith("recognized:")), None)
            alive = any(r["live_status"] == "1" for r in done)
            out[clip] = dict(costs=costs, dropped=len(rows) - len(done), total=len(rows),
                             rec_ms=float(rec[3]) if rec else None, rec_score=float(rec[2]) if rec else None,
                             accepted=bool(rec) or alive)
        return out

    def arcsoft():
        out = {}
        for clip in clips:
            rows = sorted(frames[("arcsoft", clip)], key=lambda r: int(r["frame_index"]))
            t0 = int(rows[0]["timestamp_ns"]) / 1e6
            busy, votes, alive, rec_ms, rec_score, costs, dropped = t0, [], False, None, None, [], 0
            for r in rows:
                ts = int(r["timestamp_ns"]) / 1e6
                if ts < busy:
                    dropped += 1
                    continue
                cost = (f(r["preprocess_ms"]) if f(r["preprocess_ms"]) == f(r["preprocess_ms"]) else 0) + f(r["detect_ms"])
                if int(r["face_count"] or 0) > 0 and rec_ms is None:
                    if not alive:
                        cost += f(r["liveness_ms"])
                        votes = (votes + [r["live_status"] == "1"])[-3:]
                        alive = len(votes) == 3 and sum(votes) >= 2
                    elif r["feature_ok"] == "1":
                        cost += f(r["feature_ms"])
                        s = scores.get(("arcsoft", clip, int(r["frame_index"])))
                        if s is not None and s >= a.arcsoft_threshold:
                            rec_ms, rec_score = ts + cost - t0, s
                costs.append(cost)
                busy = ts + cost
            out[clip] = dict(costs=costs, dropped=dropped, total=len(rows), rec_ms=rec_ms, rec_score=rec_score,
                             accepted=rec_ms is not None or alive)
        return out

    results = {b: session(b) for b in backends if b.startswith("session_")}
    if any(b == "arcsoft" for b in backends):
        results["arcsoft(模拟 Demo 流程)"] = arcsoft()

    print("## 每帧耗时与丢帧（只算处理了的帧）\n")
    print("| 方案 | 每帧平均 ms | P50 | P95 | 丢帧 |")
    print("|---|---|---|---|---|")
    for name, res in results.items():
        costs = [c for r in res.values() for c in r["costs"]]
        drop = sum(r["dropped"] for r in res.values()); total = sum(r["total"] for r in res.values())
        print(f"| {name} | {statistics.mean(costs):.1f} | {pct(costs, .5):.1f} | {pct(costs, .95):.1f} | {drop}/{total} |")

    print("\n## 逐片段结果\n")
    print("| 片段 | 类别 | " + " | ".join(results) + " |")
    print("|---|---|" + "---|" * len(results))
    for clip in clips:
        cells = []
        for res in results.values():
            r = res[clip]
            if label[clip] == "live":
                cells.append(f"{r['rec_ms']:.0f} ms 识别 ({r['rec_score']:.2f})" if r["rec_ms"] is not None else "未识别")
            else:
                cells.append("**放过**" if r["accepted"] else "拦截")
        print(f"| {clip} | {label[clip]} | " + " | ".join(cells) + " |")

    print("\n## 汇总\n")
    print("| 方案 | 真人识别成功 | 识别用时中位数 | 攻击放过 |")
    print("|---|---|---|---|")
    for name, res in results.items():
        live = [r for c, r in res.items() if label[c] == "live"]
        attack = [r for c, r in res.items() if label[c] != "live"]
        times = [r["rec_ms"] for r in live if r["rec_ms"] is not None]
        med = f"{statistics.median(times):.0f} ms" if times else "—"
        print(f"| {name} | {len(times)}/{len(live)} | {med} | {sum(r['accepted'] for r in attack)}/{len(attack)} |")


if __name__ == "__main__":
    main()
