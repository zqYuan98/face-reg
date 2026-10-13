"""Static int8 (QDQ, per-channel) quantization of the 1024-d recognizer, calibrated on aligned
faces from the HardHat *train* split (the golden set comes from the test split)."""
import glob, os, sys
import cv2, numpy as np, onnx, onnx.version_converter
from onnxruntime.quantization import quantize_static, QuantType, QuantFormat, CalibrationDataReader, CalibrationMethod
from onnxruntime.quantization.shape_inference import quant_pre_process
sys.path.insert(0, os.getcwd())
from face_feature_extractor import FaceFeatureExtractor

m = onnx.load("checkpoints/face_recognizer.onnx")
dims = m.graph.input[0].type.tensor_type.shape.dim
for d, v in zip(dims, [1, 3, 248, 248]):
    d.ClearField("dim_param"); d.dim_value = v
m = onnx.version_converter.convert_version(m, 13)  # per-channel DequantizeLinear needs opset >= 13
onnx.save(m, "tmp/quant/rec_fixed.onnx")
quant_pre_process("tmp/quant/rec_fixed.onnx", "tmp/quant/rec_pre.onnx", skip_symbolic_shape=True)
pre = onnx.load("tmp/quant/rec_pre.onnx")
for i, n in enumerate(pre.graph.node):
    n.name = n.name or f"n{i:03d}_{n.op_type}"  # node names are needed to exclude layers
onnx.save(pre, "tmp/quant/rec_pre.onnx")
conv_names = [n.name for n in pre.graph.node if n.op_type == "Conv"]

ex = FaceFeatureExtractor()
def tensor(aligned):
    x = cv2.resize(aligned, (248, 248))[..., ::-1].transpose(2, 0, 1)
    return x[None].astype(np.float32)
calib = []
for p in sorted(glob.glob(sys.argv[1]))[:400]:
    img = cv2.imread(p)
    if img is None: continue
    if max(img.shape[:2]) < 700: img = cv2.resize(img, None, fx=2, fy=2)
    for b in ex.detect_faces(img):
        al = ex.align_face(img, ex.landmark_detector.inference(img, (b.x1, b.y1, b.x2, b.y2)))
        if al is not None: calib.append(tensor(al))
    if len(calib) >= 200: break
print("calibration faces", len(calib))

class Reader(CalibrationDataReader):
    def __init__(self): self.it = iter(calib)
    def get_next(self):
        x = next(self.it, None)
        return None if x is None else {"_input_123": x}

variants = {
    "conv_all": [],
    "conv_skip_first": conv_names[:1],
    "conv_skip_first_last2": conv_names[:1] + conv_names[-2:],
}
for name, exclude in variants.items():
    out = f"tmp/quant/rec_int8_{name}.onnx"
    quantize_static("tmp/quant/rec_pre.onnx", out, Reader(), quant_format=QuantFormat.QDQ, per_channel=True,
                    activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
                    calibrate_method=CalibrationMethod.Percentile, op_types_to_quantize=["Conv"],
                    nodes_to_exclude=exclude, extra_options={"CalibPercentile": 99.999})
    print(out, os.path.getsize(out) / 1e6, "MB")
