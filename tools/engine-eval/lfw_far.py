"""All-pairs verification on the cached LFW features: ~29.6M impostor pairs make FAR=1e-5 measurable."""
import pickle, numpy as np
feats = pickle.load(open("tmp/lfw_features.pkl", "rb"))
keys = sorted(feats)
ident = np.array([k.rsplit("_", 1)[0] for k in keys])
_, ids = np.unique(ident, return_inverse=True)
iu = np.triu_indices(len(keys), 1)
same = ids[iu[0]] == ids[iu[1]]
print(f"{len(keys)} images, genuine pairs {same.sum()}, impostor pairs {(~same).sum()}")
def stats(enrol, probe):
    A = np.stack([feats[k][enrol] for k in keys]).astype(np.float32)
    B = A if probe == enrol else np.stack([feats[k][probe] for k in keys]).astype(np.float32)
    S = A @ B.T
    if probe != enrol: S = (S + S.T) / 2  # symmetric: each pair scored both ways, averaged
    s = S[iu]; g, n = s[same], np.sort(s[~same])
    out = []
    for far in (1e-3, 1e-4, 1e-5):
        thr = n[int(len(n) * (1 - far))]
        out.append(f"TAR@{far:.0e} {np.mean(g > thr) * 100:6.2f}% (thr {thr:.3f})")
    return "  ".join(out)
variants = ["trunc/fp32", "subpix/fp32", "tta/fp32", "trunc/int8", "subpix/int8", "tta/int8"]
for v in variants: print(f"{v:28s}", stats(v, v))
for e, p in [("trunc/fp32", "subpix/fp32"), ("trunc/fp32", "tta/fp32"), ("trunc/fp32", "trunc/int8"), ("trunc/fp32", "tta/int8"), ("tta/fp32", "tta/int8")]:
    print(f"{'mixed ' + e + ' x ' + p:28s}", stats(e, p))
