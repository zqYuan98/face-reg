"""Can the landmark net run on the tracker's (minivision) box instead of a FaceBoxes box?

Reference: FaceBoxes box -> landmarks -> align -> feature (the enrolment path).
A: minivision box -> landmarks -> feature
B: minivision box -> landmarks -> FaceBoxes-like box regressed from those landmarks -> landmarks -> feature
C: FaceBoxes run on a 2.2x crop around the minivision box -> landmarks -> feature
Reports cosine to the reference per face. Train/test split by image for the regression in B.
"""
import glob, os, sys
import cv2, ncnn, numpy as np
sys.path.insert(0, os.getcwd())
from face_feature_extractor import FaceFeatureExtractor

ASSETS = "../android-sdk/face-core/src/main/assets/detection/"
net = ncnn.Net()
net.opt.num_threads = 4
net.load_param(ASSETS + "detection.param")
net.load_model(ASSETS + "detection.bin")


def mv_detect(bgr):
    """Port of face_detector.cpp: 192 input, aspect kept, squared boxes, inclusive coords."""
    h, w = bgr.shape[:2]
    ar = w / h
    iw, ih = int(192 * np.sqrt(ar)), int(192 / np.sqrt(ar))
    mat = ncnn.Mat.from_pixels_resize(bgr, ncnn.Mat.PixelType.PIXEL_BGR, w, h, iw, ih)
    mat.substract_mean_normalize([104.0, 117.0, 123.0], [1.0, 1.0, 1.0])
    ex = net.create_extractor()
    ex.input("data", mat)
    _, out = ex.extract("detection_out")
    if out.w == 0 or out.h == 0:  # no detections: converting an empty Mat crashes the binding
        return []
    out = np.array(out).copy()
    boxes = []
    for row in out.reshape(-1, 6):
        if row[1] < 0.6: continue
        x1, y1, x2, y2 = row[2] * w, row[3] * h, row[4] * w, row[5] * h
        bw, bh = x2 - x1 + 1, y2 - y1 + 1
        size = (bw + bh) * 0.5
        if size < 64: continue
        cx, cy = x1 + bw * 0.5, y1 + bh * 0.5
        boxes.append((cx - size * 0.5, cy - size * 0.5, cx + size * 0.5 - 1, cy + size * 0.5 - 1))
    return boxes


def iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0: return 0.0
    i = w * h
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


ex = FaceFeatureExtractor()
ex.config['detection']['max_faces'] = 50


def feature_from_box(img, box):
    lm = ex.landmark_detector.inference(img, box)
    aligned = ex.align_face(img, lm)
    return None if aligned is None else ex.extract_feature(aligned), lm


def lm_features(lm):
    p = np.asarray(lm, np.float64)
    c = p.mean(0)
    s = np.linalg.norm(p[0] - p[1]) + np.linalg.norm((p[0] + p[1]) / 2 - (p[3] + p[4]) / 2)
    return np.concatenate([((p - c) / s).ravel(), [1.0]]), c, s


def target(box, c, s):
    return np.array([(box[0] - c[0]) / s, (box[1] - c[1]) / s, (box[2] - c[0]) / s, (box[3] - c[1]) / s])


rows = []  # (image index, ref feat, mv box, fb box, A feat, landmarks from mv box, img)
paths = sorted(glob.glob(sys.argv[1]))[: int(sys.argv[2])]
for k, p in enumerate(paths):
    img = cv2.imread(p)
    if img is None: continue
    if max(img.shape[:2]) < 700: img = cv2.resize(img, None, fx=2, fy=2)
    fbs = ex.detect_faces(img)
    if not fbs: continue
    mvs = mv_detect(img)
    for fb in fbs:
        fbb = (fb.x1, fb.y1, fb.x2, fb.y2)
        best = max(mvs, key=lambda m: iou(m, fbb), default=None)
        if best is None or iou(best, fbb) < 0.4: continue
        ref, _ = feature_from_box(img, fbb)
        fa, lm_a = feature_from_box(img, best)
        if ref is None or fa is None: continue
        rows.append(dict(k=k, img=img, ref=ref, mv=best, fb=fbb, A=fa, lmA=lm_a))
print(f"{len(rows)} faces matched between detectors")

# Regression box <- landmarks, fitted on even images, evaluated on odd ones.
train = [r for r in rows if r['k'] % 2 == 0]
X = []; Y = []
for r in train:
    _, lm_ref = feature_from_box(r['img'], r['fb'])
    f, c, s = lm_features(lm_ref)
    X.append(f); Y.append(target(r['fb'], c, s))
W, *_ = np.linalg.lstsq(np.array(X), np.array(Y), rcond=None)

res = {'A': [], 'B': [], 'C': []}
for r in rows:
    if r['k'] % 2 == 0: continue
    res['A'].append(float(np.dot(r['ref'], r['A'])))
    f, c, s = lm_features(r['lmA'])
    t = f @ W
    box_b = (c[0] + t[0] * s, c[1] + t[1] * s, c[0] + t[2] * s, c[1] + t[3] * s)
    fb_feat, _ = feature_from_box(r['img'], box_b)
    res['B'].append(float(np.dot(r['ref'], fb_feat)) if fb_feat is not None else np.nan)
    m = r['mv']; cx, cy = (m[0] + m[2]) / 2, (m[1] + m[3]) / 2; hs = (m[2] - m[0]) * 1.1
    H, Wd = r['img'].shape[:2]
    x0, y0, x1, y1 = int(max(0, cx - hs)), int(max(0, cy - hs)), int(min(Wd, cx + hs)), int(min(H, cy + hs))
    crop = r['img'][y0:y1, x0:x1]
    fbc = ex.detect_faces(crop)
    if fbc:
        b = fbc[0]
        fc, _ = feature_from_box(r['img'], (b.x1 + x0, b.y1 + y0, b.x2 + x0, b.y2 + y0))
        res['C'].append(float(np.dot(r['ref'], fc)) if fc is not None else np.nan)
    else:
        res['C'].append(np.nan)

for k, v in res.items():
    v = np.array(v, np.float64)
    ok = v[~np.isnan(v)]
    print(f"{k}: n={len(v)} failed={int(np.isnan(v).sum())} cos mean={ok.mean():.4f} p5={np.percentile(ok, 5):.4f} min={ok.min():.4f} frac>=0.99={np.mean(ok >= 0.99):.2f}")
