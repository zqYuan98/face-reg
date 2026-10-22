"""Feature version arcface_r50_512: SCRFD detection with 5 keypoints, ArcFace template alignment
and the IResNet-50 (w600k_r50) recognizer, on CPU or GPU.

Mirrors tools/engine-eval/insightface_ref.py, which the device engine (libfaceengine) is checked
against, so server and device features share one space: on LFW a server fp32 enrolment against a
device int8 probe scores TAR 99.57% at FAR 1e-5, the same as either side alone.

InsightFace weights are licensed for non-commercial research only and are not in the repository;
they are read from FACE_V2_MODEL_DIR.
"""
import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort

from models.imgchecker import QualityChecker, QualityOfClarity
from models.ort_options import default_threads, session_options

ort.set_default_logger_severity(3)

FEATURE_VERSION = "arcface_r50_512"
FEATURE_DIMENSION = 512

ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

# Pose5 (android face-core engine/Pose5.java): a linear fit of five-point geometry to FSANet.
YAW_PER_RATIO, PITCH_PER_RATIO, PITCH_NEUTRAL = -72.8, -53.7, 0.4609


def execution_providers(device: str, device_id: int = 0, cache_dir: Optional[str] = None) -> list:
    """ONNX Runtime providers for FACE_DEVICE=cpu|cuda|tensorrt. A GPU device that the installed
    runtime cannot provide is an error, never a silent CPU fallback."""
    device = device.lower()
    available = ort.get_available_providers()
    if device == "cpu":
        return ["CPUExecutionProvider"]
    if device == "cuda":
        wanted = "CUDAExecutionProvider"
        providers = [(wanted, {"device_id": device_id, "cudnn_conv_algo_search": "HEURISTIC"}), "CPUExecutionProvider"]
    elif device == "tensorrt":
        wanted = "TensorrtExecutionProvider"
        trt = {"device_id": device_id, "trt_fp16_enable": True, "trt_engine_cache_enable": True,
               "trt_engine_cache_path": cache_dir or "./trt_cache"}
        providers = [(wanted, trt), ("CUDAExecutionProvider", {"device_id": device_id}), "CPUExecutionProvider"]
    else:
        raise ValueError(f"FACE_DEVICE must be cpu, cuda or tensorrt, not {device!r}")
    if wanted not in available:
        raise RuntimeError(f"{wanted} is not available in this onnxruntime build ({available})")
    if hasattr(ort, "preload_dlls"):
        ort.preload_dlls()  # CUDA/cuDNN from the nvidia-* wheels when the system has none
    return providers


def _session(path: str, providers: list, threads: int) -> ort.InferenceSession:
    s = ort.InferenceSession(path, sess_options=session_options(threads), providers=providers)
    wanted = providers[0] if isinstance(providers[0], str) else providers[0][0]
    if s.get_providers()[0] != wanted:
        raise RuntimeError(f"{os.path.basename(path)} did not load on {wanted}: {s.get_providers()}")
    return s


class Scrfd:
    """SCRFD with keypoints (det_500m, det_2.5g, det_10g): 3 strides x 2 anchors."""

    def __init__(self, path, providers, threads, input_size=640, threshold=0.5, nms=0.4):
        self.session = _session(path, providers, threads)
        self.input_name = self.session.get_inputs()[0].name
        self.size, self.threshold, self.nms_threshold = input_size, threshold, nms
        self.strides, self.anchors = (8, 16, 32), 2
        self._centers = {}
        for stride in self.strides:
            n = input_size // stride
            c = np.stack(np.mgrid[:n, :n][::-1], axis=-1).astype(np.float32)
            c = (c * stride).reshape(-1, 2)
            self._centers[stride] = np.stack([c] * self.anchors, axis=1).reshape(-1, 2)

    def detect(self, bgr: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Boxes (n, 5: x1 y1 x2 y2 score) and keypoints (n, 5, 2), best score first."""
        h, w = bgr.shape[:2]
        # Fit into a size x size canvas keeping aspect, image at top-left, zero padding.
        if h > w:
            nh, nw = self.size, max(1, int(self.size * w / h))
        else:
            nw, nh = self.size, max(1, int(self.size * h / w))
        scale = nh / h
        canvas = np.zeros((self.size, self.size, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(bgr, (nw, nh))
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (self.size, self.size), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.session.run(None, {self.input_name: blob})
        scores, boxes, kpss = [], [], []
        for i, stride in enumerate(self.strides):
            sc = outs[i].reshape(-1)
            keep = np.where(sc >= self.threshold)[0]
            if keep.size == 0:
                continue
            c = self._centers[stride][keep]
            bb = outs[i + 3].reshape(-1, 4)[keep] * stride
            kp = outs[i + 6].reshape(-1, 10)[keep] * stride
            scores.append(sc[keep])
            boxes.append(np.concatenate([c - bb[:, :2], c + bb[:, 2:]], axis=1))
            kpss.append((np.tile(c, 5) + kp).reshape(-1, 5, 2))
        if not scores:
            return np.zeros((0, 5), np.float32), np.zeros((0, 5, 2), np.float32)
        scores = np.concatenate(scores)
        order = scores.argsort()[::-1]
        boxes = np.concatenate(boxes)[order] / scale
        kpss = np.concatenate(kpss)[order] / scale
        det = np.hstack([boxes, scores[order, None]])
        keep = self._nms(det)
        return det[keep].astype(np.float32), kpss[keep].astype(np.float32)

    def _nms(self, dets):
        x1, y1, x2, y2, s = dets.T
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order, keep = s.argsort()[::-1], []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]]); yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]]); yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = np.maximum(0, xx2 - xx1 + 1) * np.maximum(0, yy2 - yy1 + 1)
            order = order[np.where(inter / (areas[i] + areas[order[1:]] - inter) <= self.nms_threshold)[0] + 1]
        return keep


def umeyama_similarity(src, dst):
    """skimage SimilarityTransform.estimate (Umeyama, no reflection): 2x3 matrix src -> dst."""
    src, dst = np.asarray(src, np.float64), np.asarray(dst, np.float64)
    ms, md = src.mean(0), dst.mean(0)
    sc, dc = src - ms, dst - md
    cov = dc.T @ sc / len(src)
    U, S, Vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(cov) < 0:
        d[1] = -1
    R = U @ np.diag(d) @ Vt
    scale = (S * d).sum() / sc.var(0).sum()
    M = np.zeros((2, 3))
    M[:, :2] = scale * R
    M[:, 2] = md - scale * R @ ms
    return M


def norm_crop(bgr, kps):
    return cv2.warpAffine(bgr, umeyama_similarity(kps, ARCFACE_DST), (112, 112), borderValue=0.0)


def pose5(kps) -> Tuple[float, float, float]:
    """Yaw, pitch, roll in degrees from five keypoints (same formula and signs as the device)."""
    p = np.asarray(kps, np.float64).reshape(5, 2)
    ex, ey = p[1] - p[0]
    iod = math.hypot(ex, ey)
    if not iod > 0:
        return math.nan, math.nan, math.nan
    roll = math.atan2(ey, ex)
    c, s = math.cos(-roll), math.sin(-roll)
    r = (p - (p[0] + p[1]) / 2) @ np.array([[c, s], [-s, c]])
    mouth = (r[3] + r[4]) / 2
    if not mouth[1] > 0:
        return math.nan, math.nan, math.degrees(roll)
    midline_x = mouth[0] * (r[2, 1] / mouth[1])
    yaw = (r[2, 0] - midline_x) / iod * YAW_PER_RATIO
    pitch = (r[2, 1] / mouth[1] - PITCH_NEUTRAL) * PITCH_PER_RATIO
    return float(yaw), float(pitch), math.degrees(roll)


class ArcFace:
    def __init__(self, path, providers, threads):
        self.session = _session(path, providers, threads)
        self.input_name = self.session.get_inputs()[0].name
        batch = self.session.get_inputs()[0].shape[0]
        self.batched = not isinstance(batch, int)

    def embed(self, faces: List[np.ndarray]) -> np.ndarray:
        """Raw (unnormalised) embeddings, one row per aligned 112x112 BGR face."""
        blob = cv2.dnn.blobFromImages(faces, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        if self.batched:
            return self.session.run(None, {self.input_name: blob})[0]
        return np.concatenate([self.session.run(None, {self.input_name: blob[i:i + 1]})[0] for i in range(len(faces))])


@dataclass
class V2Face:
    box: Tuple[float, float, float, float]
    score: float
    landmarks: List[Tuple[float, float]]
    pose: Tuple[float, float, float]
    quality: Dict[str, Dict] = field(default_factory=dict)
    feature: Optional[np.ndarray] = None


@dataclass
class V2Result:
    success: bool
    reason: Optional[str]
    face: Optional[V2Face]
    face_count: int
    rotation: int
    processing_time_ms: float


@dataclass(frozen=True)
class V2Config:
    model_dir: str = "./checkpoints/insightface"
    detector: str = "det_500m.onnx"
    recognizer: str = "w600k_r50.onnx"
    device: str = "cpu"
    device_id: int = 0
    threads: int = 4
    det_size: int = 640
    det_threshold: float = 0.5
    flip: bool = False
    min_face: int = 64
    yaw: float = 30.0
    pitch: float = 25.0
    min_clarity: float = 0.10
    brightness: Tuple[float, float] = (70.0, 230.0)

    @classmethod
    def from_env(cls) -> "V2Config":
        e = os.environ.get
        device = e("FACE_DEVICE", "cpu").lower()
        return cls(
            model_dir=e("FACE_V2_MODEL_DIR", cls.model_dir),
            detector=e("FACE_V2_DETECTOR", "det_10g.onnx" if device != "cpu" else cls.detector),
            # int8 is ~1.8x faster per CPU core at the same LFW accuracy and shares the fp32 feature
            # space (server fp32 x device int8: TAR@1e-5 99.57%); GPUs run fp32.
            recognizer=e("FACE_V2_RECOGNIZER", "w600k_r50_int8.onnx" if device == "cpu" else cls.recognizer),
            device=device,
            device_id=int(e("FACE_DEVICE_ID", "0")),
            threads=default_threads(4),
            # Mirror averaging adds ~0.01 point TAR@1e-5 on LFW: free on a GPU, double cost on a CPU.
            flip=e("FACE_V2_FLIP", "0" if device == "cpu" else "1") != "0",
            min_face=int(e("FACE_V2_MIN_FACE", str(cls.min_face))),
        )


class ArcFacePipeline:
    """One image in, the largest usable face's feature out, with quality verdicts and reasons."""

    def __init__(self, config: Optional[V2Config] = None):
        self.config = config = config or V2Config.from_env()
        providers = execution_providers(config.device, config.device_id, os.path.join(config.model_dir, "trt_cache"))
        self.detector = Scrfd(os.path.join(config.model_dir, config.detector), providers, config.threads,
                              input_size=config.det_size, threshold=config.det_threshold)
        self.recognizer = ArcFace(os.path.join(config.model_dir, config.recognizer), providers, config.threads)
        self.clarity = QualityOfClarity(low_thresh=config.min_clarity)
        self.brightness = QualityChecker(config.brightness[0] - 1, config.brightness[0], config.brightness[1], config.brightness[1] + 1)
        self.model_versions = {"detector": config.detector, "recognizer": config.recognizer,
                               "device": config.device}

    def _detect_upright(self, bgr):
        """Detection on the image as decoded (EXIF already applied), then 90/270/180 turns."""
        for turn, code in ((0, None), (90, cv2.ROTATE_90_CLOCKWISE), (270, cv2.ROTATE_90_COUNTERCLOCKWISE), (180, cv2.ROTATE_180)):
            image = bgr if code is None else cv2.rotate(bgr, code)
            boxes, kpss = self.detector.detect(image)
            if len(boxes):
                return image, boxes, kpss, turn
        return bgr, np.zeros((0, 5), np.float32), np.zeros((0, 5, 2), np.float32), 0

    def _quality(self, image, box, kps) -> Tuple[Dict[str, Dict], Optional[str]]:
        cfg = self.config
        h, w = image.shape[:2]
        x1, y1 = max(0, int(box[0])), max(0, int(box[1]))
        x2, y2 = min(w, int(math.ceil(box[2]))), min(h, int(math.ceil(box[3])))
        side = float(min(box[2] - box[0], box[3] - box[1]))
        yaw, pitch, roll = pose5(kps)
        region = image[y1:y2, x1:x2]
        q: Dict[str, Dict] = {"size": {"value": float(side), "passed": side >= cfg.min_face}}
        q["pose"] = {"yaw": yaw, "pitch": pitch, "roll": roll,
                     "passed": bool(abs(yaw) <= cfg.yaw and abs(pitch) <= cfg.pitch)}
        if region.shape[0] >= 9 and region.shape[1] >= 9:
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            bright = float(self.brightness.grid_max_bright(gray, 3, 3))
            clarity = float(self.clarity.clarity_estimate(region))
        else:
            bright, clarity = 0.0, 0.0
        q["brightness"] = {"value": bright, "passed": cfg.brightness[0] <= bright <= cfg.brightness[1]}
        q["clarity"] = {"value": clarity, "passed": clarity >= cfg.min_clarity}
        q["cropped"] = {"passed": bool(box[0] >= 0 and box[1] >= 0 and box[2] <= w and box[3] <= h)}
        q["size"]["passed"] = bool(q["size"]["passed"])
        reason = None
        if not q["size"]["passed"]:
            reason = "FACE_TOO_SMALL"
        elif not q["pose"]["passed"]:
            reason = "BAD_POSE"
        elif not q["brightness"]["passed"]:
            reason = "TOO_DARK" if bright < cfg.brightness[0] else "TOO_BRIGHT"
        elif not q["clarity"]["passed"]:
            reason = "TOO_BLURRY"
        return q, reason

    def extract(self, bgr: np.ndarray, quality_filter: bool = True) -> V2Result:
        started = time.perf_counter()
        elapsed = lambda: (time.perf_counter() - started) * 1000
        image, boxes, kpss, turn = self._detect_upright(bgr)
        if not len(boxes):
            return V2Result(False, "NO_FACE", None, 0, 0, elapsed())
        # The largest face is the subject; a smaller bystander does not block enrolment.
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        i = int(np.argmax(areas))
        box, kps = boxes[i], kpss[i]
        quality, reason = self._quality(image, box, kps)
        face = V2Face(box=tuple(float(v) for v in box[:4]), score=float(box[4]),
                      landmarks=[(float(x), float(y)) for x, y in kps], pose=(quality["pose"]["yaw"],
                      quality["pose"]["pitch"], quality["pose"]["roll"]), quality=quality)
        if quality_filter and reason is not None:
            return V2Result(False, reason, face, len(boxes), turn, elapsed())
        face.feature = self.embed(image, kps)
        if face.feature is None:
            return V2Result(False, "FEATURE_FAILED", face, len(boxes), turn, elapsed())
        return V2Result(True, None, face, len(boxes), turn, elapsed())

    def embed(self, image: np.ndarray, kps) -> Optional[np.ndarray]:
        """Unit-length feature of the face at {@code kps}; with flip, the sum of both mirror images."""
        aligned = norm_crop(image, kps)
        faces = [aligned, cv2.flip(aligned, 1)] if self.config.flip else [aligned]
        raw = self.recognizer.embed(faces).sum(axis=0).astype(np.float32)
        norm = float(np.linalg.norm(raw))
        return raw / norm if np.isfinite(norm) and norm > 0 else None
