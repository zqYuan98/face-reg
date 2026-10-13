import glob, os, sys
import cv2, numpy as np, onnxruntime as ort
ort.set_default_logger_severity(3)
sys.path.insert(0, os.getcwd())
from face_feature_extractor import FaceFeatureExtractor
ex = FaceFeatureExtractor()
def sess(p):
    so = ort.SessionOptions(); so.intra_op_num_threads = 4
    return ort.InferenceSession(p, so)
def feat(s, al):
    x = cv2.resize(al, (248, 248))[..., ::-1].transpose(2, 0, 1)[None].astype(np.float32)
    f = np.sqrt(s.run(None, {"_input_123": x})[0].ravel()); return f / np.linalg.norm(f)
models = {k: sess(v) for k, v in [("fp32", "checkpoints/face_recognizer.onnx")] + [(os.path.basename(p), p) for p in sorted(glob.glob("tmp/quant/rec_int8_*.onnx"))]}
cos = {k: [] for k in models if k != "fp32"}
for p in sorted(glob.glob("tmp/golden_png/*.png")):
    img = cv2.imread(p)
    for b in ex.detect_faces(img):
        al = ex.align_face(img, ex.landmark_detector.inference(img, (b.x1, b.y1, b.x2, b.y2)))
        ref = feat(models["fp32"], al)
        for k in cos: cos[k].append(float(ref @ feat(models[k], al)))
for k, v in cos.items():
    v = np.array(v); print(f"{k}: n={len(v)} min {v.min():.4f} p5 {np.percentile(v,5):.4f} median {np.median(v):.4f}")
