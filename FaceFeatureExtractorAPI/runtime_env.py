"""Native thread limits. Import before numpy, cv2 or onnxruntime are loaded."""
import os

# The pipeline only does tiny matrix work in NumPy, yet OpenBLAS and OpenMP start one thread per
# core in every process; with several workers those pools fight over the cores. ONNX Runtime
# threads are set separately by FACE_ORT_THREADS.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "1")


def limit_opencv():
    import cv2
    cv2.setNumThreads(int(os.environ.get("FACE_CV_THREADS", "1")))
