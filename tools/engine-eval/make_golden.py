"""Writes lossless PNG inputs and the Python service's reference outputs (no rotation step)."""
import glob, os, sys
import cv2, numpy as np
sys.path.insert(0, os.getcwd())
from face_feature_extractor import FaceFeatureExtractor
src = sorted(glob.glob(sys.argv[1]))[: int(sys.argv[2])]
ex = FaceFeatureExtractor()
lines = []
for k, p in enumerate(src):
    img = cv2.imread(p)
    if img is None: continue
    if max(img.shape[:2]) < 700: img = cv2.resize(img, None, fx=2, fy=2)
    name = f"g{k:04d}.png"
    cv2.imwrite(os.path.join("tmp/golden_png", name), img)
    lines.append(f"IMG /data/local/tmp/fe/img/{name}")
    boxes = ex.detect_faces(img)
    if not boxes:
        lines.append("NOFACE"); continue
    b = boxes[0]
    lines.append("BOX %.9g %.9g %.9g %.9g %.9g" % (b.x1, b.y1, b.x2, b.y2, b.score))
    lm = ex.landmark_detector.inference(img, (b.x1, b.y1, b.x2, b.y2))
    lines.append("LM " + " ".join("%.17g %.17g" % (x, y) for x, y in lm))
    al = ex.align_face(img, lm)
    lines.append("ALIGNSUM %.0f" % al.astype(np.float64).sum())
    f = ex.extract_feature(al)
    lines.append("FEAT " + " ".join("%.9g" % v for v in f))
open("tmp/golden_python.txt", "w").write("\n".join(lines) + "\n")
with open("tmp/golden_list.txt", "w", newline="\n") as fh:
    fh.write("\n".join(l[4:] for l in lines if l.startswith("IMG ")) + "\n")
print(sum(l.startswith("FEAT") for l in lines), "features from", sum(l.startswith("IMG") for l in lines), "images")
