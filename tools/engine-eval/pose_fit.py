import glob, os, sys, numpy as np, cv2
sys.path.insert(0, os.getcwd())
from face_feature_extractor import FaceFeatureExtractor
sys.argv = [sys.argv[0], "D:/face-deps/lfw"]
exec(open("tmp/lfw_eval.py").read().split("def tta_landmarks")[0].split("LFW = sys.argv[1]")[0])  # imports only
from models.facelandmarks5er import crop_face, shape_index_process
ex = FaceFeatureExtractor(); lmk = ex.landmark_detector
def landmarks(img, box):
    H, W = img.shape[:2]; face, box = crop_face(img, box, H, W)
    g = cv2.cvtColor(cv2.resize(face, (112, 112)), cv2.COLOR_BGR2GRAY).reshape(1, 1, 112, 112).astype(np.float32)
    r1 = lmk.ort_session1.run([], {lmk.first_input_name: g}); r2 = lmk.ort_session2.run([], {lmk.second_input_name: shape_index_process(r1[0], r1[1])})
    pts = ((r2[0] + r1[1]) * 112).reshape(-1); sx, sy = (box[2] - box[0]) / 112, (box[3] - box[1]) / 112
    return np.array([[box[0] + pts[2*i] * sx, box[1] + pts[2*i+1] * sy] for i in range(5)])
def ratios(p):
    e = p[1] - p[0]; iod = np.hypot(*e); roll = np.arctan2(e[1], e[0]); o = (p[0] + p[1]) / 2
    c, s = np.cos(-roll), np.sin(-roll); r = np.array([[(x - o[0]) * c - (y - o[1]) * s, (x - o[0]) * s + (y - o[1]) * c] for x, y in p])
    mx, my = (r[3] + r[4]) / 2; mid = mx * (r[2][1] / my)
    return (r[2][0] - mid) / iod, r[2][1] / my
X, Y = [], []
paths = sorted(glob.glob("tmp/golden_png/*.png")) + sorted(glob.glob("D:/face-deps/lfw/lfw/*/*_0001.jpg"))[:1500]
for p in paths:
    img = cv2.imread(p)
    for b in ex.detect_faces(img)[:1]:
        pts = landmarks(img, (b.x1, b.y1, b.x2, b.y2))
        al = ex.align_face(img, [tuple(q) for q in pts])
        yaw, pitch, roll = ex.pose_checker.detect_angle(al)
        X.append(ratios(pts)); Y.append((yaw, pitch))
X, Y = np.array(X), np.array(Y)
ky = np.linalg.lstsq(np.c_[X[:, 0]], Y[:, 0], rcond=None)[0][0]
A = np.c_[X[:, 1], np.ones(len(X))]; kp, b = np.linalg.lstsq(A, Y[:, 1], rcond=None)[0]
pred_y, pred_p = X[:, 0] * ky, X[:, 1] * kp + b
print(f"n={len(X)}  yaw: deg = {ky:.1f} * ratio  (corr {np.corrcoef(pred_y, Y[:,0])[0,1]:.3f}, MAE {np.mean(abs(pred_y - Y[:,0])):.1f} deg, fsanet |yaw| p95 {np.percentile(abs(Y[:,0]),95):.1f})")
print(f"pitch: deg = {kp:.1f} * (ratio - {-b/kp:.4f})  (corr {np.corrcoef(pred_p, Y[:,1])[0,1]:.3f}, MAE {np.mean(abs(pred_p - Y[:,1])):.1f} deg)")
gate_fs = (abs(Y[:,0]) <= 30) & (abs(Y[:,1]) <= 25); gate_g = (abs(pred_y) <= 30) & (abs(pred_p) <= 25)
print(f"gate agreement with FSANet (30/25 deg): {np.mean(gate_fs == gate_g):.3f}; FSANet rejects {np.mean(~gate_fs):.3f}, geometric rejects {np.mean(~gate_g):.3f}")
