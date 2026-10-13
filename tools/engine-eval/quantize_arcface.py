"""Static int8 (QDQ, per-channel, convolutions only, first conv kept float) for an ArcFace ONNX
model, calibrated on SCRFD-aligned 112x112 faces from an image glob that is NOT the evaluation set.

  python quantize_arcface.py <model.onnx> <out.onnx> <scrfd.onnx> "<calibration glob>" [count]
"""
import glob, os, sys
import cv2, numpy as np, onnx, onnx.version_converter
from onnxruntime.quantization import (CalibrationDataReader, CalibrationMethod, QuantFormat, QuantType,
                                      quantize_static)
from onnxruntime.quantization.shape_inference import quant_pre_process
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from insightface_ref import Scrfd, norm_crop

src, out, det_path, pattern = sys.argv[1:5]
count = int(sys.argv[5]) if len(sys.argv) > 5 else 300
work = out + ".work"
os.makedirs(work, exist_ok=True)

m = onnx.load(src)
for d, v in zip(m.graph.input[0].type.tensor_type.shape.dim, [1, 3, 112, 112]):
    d.ClearField("dim_param"); d.dim_value = v
if m.opset_import[0].version < 13:
    m = onnx.version_converter.convert_version(m, 13)
onnx.save(m, os.path.join(work, "fixed.onnx"))
quant_pre_process(os.path.join(work, "fixed.onnx"), os.path.join(work, "pre.onnx"), skip_symbolic_shape=True)
pre = onnx.load(os.path.join(work, "pre.onnx"))
for i, n in enumerate(pre.graph.node):
    n.name = n.name or f"n{i:04d}_{n.op_type}"
onnx.save(pre, os.path.join(work, "pre.onnx"))
convs = [n.name for n in pre.graph.node if n.op_type == "Conv"]
input_name = pre.graph.input[0].name

det = Scrfd(det_path)
calib = []
for p in sorted(glob.glob(pattern)):
    img = cv2.imread(p)
    if img is None: continue
    if max(img.shape[:2]) < 700: img = cv2.resize(img, None, fx=2, fy=2)
    boxes, kpss = det.detect(img)
    for k in kpss[:2]:
        calib.append(cv2.dnn.blobFromImage(norm_crop(img, k), 1 / 127.5, (112, 112), (127.5,) * 3, swapRB=True))
    if len(calib) >= count: break
print("calibration faces", len(calib))


class Reader(CalibrationDataReader):
    def __init__(self): self.it = iter(calib)
    def get_next(self):
        x = next(self.it, None)
        return None if x is None else {input_name: x}


quantize_static(os.path.join(work, "pre.onnx"), out, Reader(), quant_format=QuantFormat.QDQ, per_channel=True,
                activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
                calibrate_method=CalibrationMethod.Percentile, op_types_to_quantize=["Conv"],
                nodes_to_exclude=convs[:1], extra_options={"CalibPercentile": 99.999})
print(out, os.path.getsize(out) / 1e6, "MB")
