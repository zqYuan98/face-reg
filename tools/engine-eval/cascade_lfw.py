"""MobileFaceNet -> IResNet-50 cascade on LFW all pairs (cached features from insightface_lfw.py).

Rule per pair: s1 = MobileFaceNet cosine. s1 >= accept -> match without R50; s1 < reject -> no
match without R50; otherwise decide with the R50 cosine at R50's own threshold. Reports, per
accept threshold, the cascade's TAR at the target FAR, how often R50 is skipped, and the overall
FAR actually reached, next to R50 alone.

    python cascade_lfw.py [--cache tmp/lfw_features.pkl] [--far 1e-5]
"""
import argparse, pickle
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--cache", default="tmp/lfw_features.pkl")
ap.add_argument("--far", type=float, default=1e-5)
ap.add_argument("--fast", default="if_mbf")
ap.add_argument("--slow", default="if_r50_int8_d500")
a = ap.parse_args()

feats = pickle.load(open(a.cache, "rb"))
keys = sorted(feats)
_, ids = np.unique([k.rsplit("_", 1)[0] for k in keys], return_inverse=True)
iu = np.triu_indices(len(keys), 1)
same = ids[iu[0]] == ids[iu[1]]


def scores(name):
    m = np.stack([feats[k][name] for k in keys]).astype(np.float32)
    return (m @ m.T)[iu]


s1, s2 = scores(a.fast), scores(a.slow)
g1, n1, g2, n2 = s1[same], s1[~same], s2[same], s2[~same]
N = len(n2)


def thr_at(neg, far):
    return np.sort(neg)[min(len(neg) - 1, int(len(neg) * (1 - far)))]


t2 = thr_at(n2, a.far)
t1_alone = thr_at(n1, a.far)
print(f"{len(keys)} images, genuine {same.sum()}, impostor {N}; target FAR {a.far:g}")
print(f"R50 alone:  thr {t2:.3f}  TAR {np.mean(g2 > t2) * 100:.2f}%  (R50 run for every probe)")
print(f"MBF alone:  thr {t1_alone:.3f}  TAR {np.mean(g1 > t1_alone) * 100:.2f}%")
print()
print("| accept (MBF FAR) | reject | TAR | FAR reached | genuine decided by MBF | impostors decided by MBF |")
print("|---|---|---|---|---|---|")
for accept_far in (1e-6, 3e-6, 1e-5 * 0.5):
    accept = thr_at(n1, accept_far)
    for reject_q in (0.001, 0.005):
        reject = np.quantile(g1, reject_q)  # lose at most this share of genuine pairs to an MBF reject
        if reject >= accept:
            continue
        acc = (s1 >= accept) | ((s1 >= reject) & (s1 < accept) & (s2 > t2))
        tar, far = np.mean(acc[same]) * 100, np.mean(acc[~same])
        mbf_g = np.mean((g1 >= accept) | (g1 < reject)) * 100
        mbf_n = np.mean((n1 >= accept) | (n1 < reject)) * 100
        print(f"| {accept:.3f} ({accept_far:.0e}) | {reject:.3f} (genuine q{reject_q:g}) | {tar:.2f}% | {far:.1e} | {mbf_g:.1f}% | {mbf_n:.1f}% |")
