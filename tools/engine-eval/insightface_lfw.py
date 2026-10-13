"""Adds InsightFace features to the LFW feature cache made by lfw_eval.py, then reports all-pairs
TAR@FAR for them next to the v1 baseline. Run from FaceFeatureExtractorAPI/.

  python insightface_lfw.py <lfw_dir> <insightface_models_dir>
"""
import os, pickle, sys, time
import cv2, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from insightface_ref import Scrfd, ArcFace, norm_crop

LFW, MODELS = sys.argv[1], sys.argv[2]
CACHE = "tmp/lfw_features.pkl"
packs = {"if_r50": ("buffalo_l/det_10g.onnx", "buffalo_l/w600k_r50.onnx"),
         "if_mbf": ("buffalo_s/det_500m.onnx", "buffalo_s/w600k_mbf.onnx"),
         # Device combinations: the small detector with each recognizer, fp32 and int8.
         "if_r50_d500": ("buffalo_s/det_500m.onnx", "buffalo_l/w600k_r50.onnx"),
         "if_r50_int8_d500": ("buffalo_s/det_500m.onnx", "w600k_r50_int8.onnx"),
         "if_mbf_int8": ("buffalo_s/det_500m.onnx", "w600k_mbf_int8.onnx")}
feats = pickle.load(open(CACHE, "rb"))
keys = sorted(feats)
for name, (det_path, rec_path) in packs.items():
    if all(name in feats[k] for k in keys): continue
    det, rec = Scrfd(os.path.join(MODELS, det_path)), ArcFace(os.path.join(MODELS, rec_path))
    misses, t0 = 0, time.time()
    for i, key in enumerate(keys):
        person = key.rsplit("_", 1)[0]
        img = cv2.imread(os.path.join(LFW, "lfw", person, key + ".jpg"))
        boxes, kpss = det.detect(img)
        if len(boxes) == 0:  # fall back to a centred canonical layout, as LFW faces are centred
            misses += 1
            kps = np.array([[100, 115], [150, 115], [125, 140], [105, 165], [145, 165]], np.float32)
        else:
            c = ((boxes[:, 0] + boxes[:, 2]) / 2 - 125) ** 2 + ((boxes[:, 1] + boxes[:, 3]) / 2 - 125) ** 2
            kps = kpss[int(np.argmin(c))]
        feats[key][name] = rec.feature(norm_crop(img, kps))
        if i % 2000 == 0: print(name, i, f"{time.time() - t0:.0f}s", flush=True)
    print(name, "detector misses:", misses, flush=True)
    pickle.dump(feats, open(CACHE, "wb"))

ident = np.array([k.rsplit("_", 1)[0] for k in keys])
_, ids = np.unique(ident, return_inverse=True)
iu = np.triu_indices(len(keys), 1)
same = ids[iu[0]] == ids[iu[1]]
print(f"{len(keys)} images, genuine {same.sum()}, impostor {(~same).sum()}")
for variant in ("trunc/fp32", "tta/int8", "if_mbf", "if_mbf_int8", "if_r50", "if_r50_d500", "if_r50_int8_d500"):
    A = np.stack([feats[k][variant] for k in keys]).astype(np.float32)
    s = (A @ A.T)[iu]
    g, n = s[same], np.sort(s[~same])
    row = []
    for far in (1e-3, 1e-4, 1e-5, 1e-6):
        thr = n[min(len(n) - 1, int(len(n) * (1 - far)))]
        row.append(f"TAR@{far:.0e} {np.mean(g > thr) * 100:6.2f}% (thr {thr:.3f})")
    print(f"{variant:12s}", "  ".join(row), flush=True)
