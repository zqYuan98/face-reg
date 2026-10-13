"""Reference InsightFace pipeline (SCRFD + 5-point similarity alignment + ArcFace), mirroring
insightface/python-package model_zoo/scrfd.py, utils/face_align.py and arcface_onnx.py.
The C++ engine is checked against this file. Models are research-licensed: evaluation only."""
import cv2
import numpy as np
import onnxruntime as ort

ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def session(path, threads=8):
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    return ort.InferenceSession(path, options)


class Scrfd:
    """SCRFD with keypoints (det_10g, det_500m): 3 strides x 2 anchors, outputs ordered
    scores(8,16,32), boxes(8,16,32), kps(8,16,32)."""

    def __init__(self, path, input_size=640, threshold=0.5, nms=0.4):
        self.s = session(path)
        self.input_name = self.s.get_inputs()[0].name
        self.size = input_size
        self.threshold, self.nms_threshold = threshold, nms
        self.strides, self.anchors = (8, 16, 32), 2
        self._centers = {}

    def centers(self, h, w, stride):
        key = (h, w, stride)
        if key not in self._centers:
            c = np.stack(np.mgrid[:h, :w][::-1], axis=-1).astype(np.float32)
            c = (c * stride).reshape(-1, 2)
            self._centers[key] = np.stack([c] * self.anchors, axis=1).reshape(-1, 2)
        return self._centers[key]

    def detect(self, bgr, max_num=0):
        h, w = bgr.shape[:2]
        # Fit into a size x size canvas keeping aspect, image at top-left, zero padding.
        if h / w > 1:
            nh, nw = self.size, int(self.size / (h / w))
        else:
            nw, nh = self.size, int(self.size * (h / w))
        scale = nh / h
        canvas = np.zeros((self.size, self.size, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(bgr, (nw, nh))
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (self.size, self.size), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.s.run(None, {self.input_name: blob})
        scores, boxes, kpss = [], [], []
        for i, stride in enumerate(self.strides):
            sc = outs[i].reshape(-1)
            bb = outs[i + 3].reshape(-1, 4) * stride
            kp = outs[i + 6].reshape(-1, 10) * stride
            ctr = self.centers(self.size // stride, self.size // stride, stride)
            keep = np.where(sc >= self.threshold)[0]
            c = ctr[keep]
            scores.append(sc[keep])
            boxes.append(np.concatenate([c - bb[keep, :2], c + bb[keep, 2:]], axis=1))
            kpss.append((np.tile(c, 5) + kp[keep]).reshape(-1, 5, 2))
        scores = np.concatenate(scores)
        if scores.size == 0:
            return np.zeros((0, 5)), np.zeros((0, 5, 2))
        order = scores.argsort()[::-1]
        boxes = np.concatenate(boxes)[order] / scale
        kpss = np.concatenate(kpss)[order] / scale
        scores = scores[order]
        keep = self.nms(np.hstack([boxes, scores[:, None]]))
        det = np.hstack([boxes, scores[:, None]])[keep]
        return det.astype(np.float32), kpss[keep].astype(np.float32)

    def nms(self, dets):
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
    if np.linalg.det(cov) < 0: d[1] = -1
    R = U @ np.diag(d) @ Vt
    scale = (S * d).sum() / sc.var(0).sum()
    M = np.zeros((2, 3))
    M[:, :2] = scale * R
    M[:, 2] = md - scale * R @ ms
    return M


def norm_crop(bgr, kps):
    return cv2.warpAffine(bgr, umeyama_similarity(kps, ARCFACE_DST), (112, 112), borderValue=0.0)


class ArcFace:
    def __init__(self, path):
        self.s = session(path)
        self.input_name = self.s.get_inputs()[0].name

    def feature(self, aligned_bgr):
        blob = cv2.dnn.blobFromImage(aligned_bgr, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        f = self.s.run(None, {self.input_name: blob})[0].ravel().astype(np.float32)
        return f / np.linalg.norm(f)
