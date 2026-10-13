"""LFW 6000-pair verification for pipeline variants of the v1 recognizer (internal evaluation only).

Variants differ only in how landmarks are produced and which recognizer weights run:
  trunc   : landmarks truncated to whole 112-crop units (current enrolment service)
  subpix  : landmark fractions kept
  tta     : subpix, averaged over 5 jittered boxes
and recognizer fp32 vs int8. "mixed" pairs score an enrolment-side variant against a probe-side one.
"""
import os, sys, pickle, time
import cv2, numpy as np, onnxruntime as ort
ort.set_default_logger_severity(3)
sys.path.insert(0, os.getcwd())
from face_feature_extractor import FaceFeatureExtractor
from models.facelandmarks5er import crop_face, shape_index_process

LFW = sys.argv[1]
INT8 = sys.argv[2] if len(sys.argv) > 2 else None
CACHE = "tmp/lfw_features.pkl"
ex = FaceFeatureExtractor()
lmk = ex.landmark_detector


def landmarks(img, box, subpixel):
    """Landmark5er.inference with optional sub-pixel output."""
    H, W = img.shape[:2]
    face, box = crop_face(img, box, H, W)
    gray = cv2.cvtColor(cv2.resize(face, (112, 112)), cv2.COLOR_BGR2GRAY).reshape(1, 1, 112, 112).astype(np.float32)
    r1 = lmk.ort_session1.run([], {lmk.first_input_name: gray})
    r2 = lmk.ort_session2.run([], {lmk.second_input_name: shape_index_process(r1[0], r1[1])})
    pts = ((r2[0] + r1[1]) * 112).reshape(-1)
    if not subpixel: pts = pts.astype(np.int32)
    sx, sy = (box[2] - box[0]) / 112.0, (box[3] - box[1]) / 112.0
    return [(max(0.01, min(box[0] + pts[2 * i] * sx, W - 0.01)), max(0.01, min(box[1] + pts[2 * i + 1] * sy, H - 0.01)))
            for i in range(5)]


def tta_landmarks(img, box):
    x1, y1, x2, y2 = box; w, h = x2 - x1, y2 - y1
    acc = []
    for dx, dy, ds in [(0, 0, 0), (0.03, 0, 0), (-0.03, 0, 0), (0, 0.03, 0.04), (0, -0.03, -0.04)]:
        cx, cy = (x1 + x2) / 2 + dx * w, (y1 + y2) / 2 + dy * h
        hw, hh = w * (1 + ds) / 2, h * (1 + ds) / 2
        acc.append(np.array(landmarks(img, (cx - hw, cy - hh, cx + hw, cy + hh), True)))
    return [tuple(p) for p in np.mean(acc, axis=0)]


def session(path):
    so = ort.SessionOptions(); so.intra_op_num_threads = 8
    return ort.InferenceSession(path, so)


recognizers = {"fp32": session("checkpoints/face_recognizer.onnx")}
if INT8: recognizers["int8"] = session(INT8)


def feature(sess, aligned):
    x = cv2.resize(aligned, (248, 248))[..., ::-1].transpose(2, 0, 1)[None].astype(np.float32)
    f = np.sqrt(sess.run(None, {sess.get_inputs()[0].name: x})[0].ravel())
    return f / np.linalg.norm(f)


pairs = []
for line in open(os.path.join(LFW, "pairs.txt")).read().split("\n")[1:]:
    t = line.split()
    if len(t) == 3: pairs.append(((t[0], int(t[1])), (t[0], int(t[2])), 1))
    elif len(t) == 4: pairs.append(((t[0], int(t[1])), (t[2], int(t[3])), 0))
images = sorted({p for a, b, _ in pairs for p in (a, b)})
print(len(pairs), "pairs,", len(images), "images")

feats = pickle.load(open(CACHE, "rb")) if os.path.exists(CACHE) else {}
t0, misses = time.time(), 0
for k, (name, idx) in enumerate(images):
    key = f"{name}_{idx:04d}"
    if key in feats and all(f"{v}/{r}" in feats[key] for v in ("trunc", "subpix", "tta") for r in recognizers): continue
    img = cv2.imread(os.path.join(LFW, "lfw", name, key + ".jpg"))
    boxes = ex.face_detector.detect(img, 0.35, 0.45, 20)
    if boxes:  # the LFW subject is the face nearest the image centre
        b = min(boxes, key=lambda b: ((b.x1 + b.x2) / 2 - 125) ** 2 + ((b.y1 + b.y2) / 2 - 125) ** 2)
        box = (b.x1, b.y1, b.x2, b.y2)
    else:
        misses += 1; box = (70.0, 60.0, 180.0, 190.0)
    lms = {"trunc": landmarks(img, box, False), "subpix": landmarks(img, box, True), "tta": tta_landmarks(img, box)}
    entry = feats.setdefault(key, {})
    for v, lm in lms.items():
        al = ex.align_face(img, lm)
        for r, s in recognizers.items(): entry[f"{v}/{r}"] = feature(s, al)
    if k % 1000 == 0: print(k, f"{time.time() - t0:.0f}s"); pickle.dump(feats, open(CACHE, "wb"))
pickle.dump(feats, open(CACHE, "wb"))
print("detector misses (centre box used):", misses)


def report(label, enrol, probe):
    s = np.array([feats[f"{a[0]}_{a[1]:04d}"][enrol] @ feats[f"{b[0]}_{b[1]:04d}"][probe] for a, b, _ in pairs])
    y = np.array([l for _, _, l in pairs])
    accs = []
    for fold in range(10):  # standard protocol: threshold picked on 9 folds, tested on the 10th
        test = np.zeros(len(pairs), bool); test[fold * 600:(fold + 1) * 600] = True
        ths = np.unique(s[~test])
        best = ths[np.argmax([np.mean((s[~test] >= t) == y[~test]) for t in ths[::7]] ) * 7]
        accs.append(np.mean((s[test] >= best) == y[test]))
    neg = np.sort(s[y == 0]); thr = neg[int(len(neg) * 0.999)]
    print(f"{label:34s} acc {np.mean(accs) * 100:.2f}%  TAR@FAR=1e-3 {np.mean(s[y == 1] > thr) * 100:.2f}%  "
          f"genuine mean {s[y == 1].mean():.4f}  impostor mean {s[y == 0].mean():.4f}")


for r in recognizers:
    for v in ("trunc", "subpix", "tta"): report(f"{v}/{r}", f"{v}/{r}", f"{v}/{r}")
for v in ("subpix", "tta"): report(f"mixed enrol trunc/fp32 probe {v}/fp32", "trunc/fp32", f"{v}/fp32")
if INT8:
    for v in ("trunc", "subpix", "tta"): report(f"mixed enrol {v}/fp32 probe {v}/int8", f"{v}/fp32", f"{v}/int8")
