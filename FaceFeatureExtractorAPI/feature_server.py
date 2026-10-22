# -*- coding: utf-8 -*-
"""Feature API: bounded model execution with independent liveness and readiness."""
import runtime_env  # noqa: F401  (thread limits must precede numpy/cv2/onnxruntime)
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bounded_inference import BoundedInference

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("FeatureServer")


V1_VERSION = "recognizer_v1_1024"
V2_VERSION = "arcface_r50_512"


def enabled_versions():
    """FACE_FEATURE_VERSIONS: comma-separated feature versions this process loads."""
    raw = os.getenv("FACE_FEATURE_VERSIONS", V1_VERSION)
    versions = {v.strip() for v in raw.split(",") if v.strip()}
    unknown = versions - {V1_VERSION, V2_VERSION}
    if unknown or not versions:
        raise ValueError(f"FACE_FEATURE_VERSIONS has unsupported values: {sorted(unknown) or raw!r}")
    return versions


class ModelSet:
    """The loaded pipelines. v1 calls keep the original extractor interface."""

    def __init__(self, v1=None, v2=None):
        self.v1, self.v2 = v1, v2

    def _require_v1(self):
        if self.v1 is None:
            raise HTTPException(404, detail={"errorCode": "FEATURE_VERSION_DISABLED"})
        return self.v1

    def extract_features(self, *args, **kwargs):
        return self._require_v1().extract_features(*args, **kwargs)

    def detect_faces(self, *args, **kwargs):
        return self._require_v1().detect_faces(*args, **kwargs)


def default_factory():
    # Heavy native imports and model loading run in the bounded worker, never on the event loop.
    runtime_env.limit_opencv()
    versions = enabled_versions()
    v1 = v2 = None
    if V1_VERSION in versions:
        from face_feature_extractor import FaceFeatureExtractor
        v1 = FaceFeatureExtractor()
    if V2_VERSION in versions:
        from models.arcface_pipeline import ArcFacePipeline
        v2 = ArcFacePipeline()
    return ModelSet(v1, v2)


def _finite(value):
    """JSON cannot carry NaN; undefined measurements (e.g. pose of a degenerate face) become null."""
    if hasattr(value, "item") and not isinstance(value, (list, tuple, dict)) and getattr(value, "ndim", 1) == 0:
        value = value.item()  # numpy scalar
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def v2_response(result, model_versions):
    face = result.face
    body = {
        "success": result.success,
        "reason_code": result.reason,
        "feature_version": V2_VERSION,
        "feature": None,
        "feature_dim": None,
        "face": None,
        "face_count": result.face_count,
        "rotation": result.rotation,
        "model_versions": model_versions,
        "processing_time_ms": result.processing_time_ms,
    }
    if face is not None:
        x1, y1, x2, y2 = face.box
        body["face"] = {
            "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            "score": face.score,
            "landmarks": [list(point) for point in face.landmarks],
            "pose": {"yaw": face.pose[0], "pitch": face.pose[1], "roll": face.pose[2]},
            "quality": face.quality,
        }
        if face.feature is not None:
            body["feature"] = face.feature.tolist()
            body["feature_dim"] = len(body["feature"])
    return _finite(body)


def decode_image(contents):
    import cv2
    import numpy as np
    decoded = cv2.imdecode(np.frombuffer(contents, np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(400, detail={"errorCode": "INVALID_IMAGE"})
    if decoded.shape[0] * decoded.shape[1] > 25_000_000:
        raise HTTPException(413, detail={"errorCode": "IMAGE_DIMENSIONS_TOO_LARGE"})
    return decoded


class ExtractFeatureResponse(BaseModel):
    success: bool
    message: str
    feature: Optional[List[float]] = None
    feature_dim: Optional[int] = None
    processing_time: Optional[float] = None


class FaceRect(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    score: float


class DetectFaceResponse(BaseModel):
    success: bool
    message: str
    faces: List[FaceRect] = Field(default_factory=list)
    processing_time: Optional[float] = None


def create_app(factory=default_factory, decoder=decode_image, *, timeout_seconds=None,
               unhealthy_after_seconds=None, max_image_bytes=None, queue_limit=None):
    runtime = BoundedInference(
        factory,
        timeout_seconds=float(os.getenv("FACE_INFERENCE_TIMEOUT_SECONDS", "12")) if timeout_seconds is None else timeout_seconds,
        unhealthy_after_seconds=float(os.getenv("FACE_INFERENCE_UNHEALTHY_SECONDS", "30")) if unhealthy_after_seconds is None else unhealthy_after_seconds,
        max_image_bytes=int(os.getenv("FACE_MAX_IMAGE_BYTES", str(10 * 1024 * 1024))) if max_image_bytes is None else max_image_bytes,
        queue_limit=int(os.getenv("FACE_INFERENCE_QUEUE", "32")) if queue_limit is None else queue_limit,
    )

    @asynccontextmanager
    async def lifespan(application):
        runtime.start()
        try:
            yield
        finally:
            runtime.close()

    application = FastAPI(title="Face Feature Extraction Microservice", version="1.1.0", lifespan=lifespan)
    application.state.inference = runtime
    # This service is called by the Java backend, not cross-origin browsers. No wildcard CORS.

    @application.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "Face Feature Extractor"}

    @application.get("/ready")
    async def readiness_check():
        state = runtime.readiness()
        return JSONResponse(state, status_code=200 if state["status"] == "ready" else 503)

    def extraction(model, contents):
        decoded = decoder(contents)
        result = model.extract_features(decoded, return_all_faces=False, quality_filter=True)
        if not result.success or not result.faces:
            return ExtractFeatureResponse(success=False, message="No usable face detected", processing_time=result.processing_time)
        feature = result.faces[0].feature.tolist()
        if len(feature) != 1024 or not all(math.isfinite(value) for value in feature) or not any(feature):
            raise HTTPException(502, detail={"errorCode": "INVALID_FEATURE"})
        return ExtractFeatureResponse(success=True, message="Success", feature=feature, feature_dim=len(feature), processing_time=result.processing_time)

    async def execute(upload, operation):
        try:
            return await runtime.execute(upload, operation)
        except HTTPException:
            raise
        except Exception as error:
            logger.error("Inference failed: %s", type(error).__name__)
            raise HTTPException(500, detail={"errorCode": "INFERENCE_FAILED"}) from None

    @application.post("/api/extract_feature", response_model=ExtractFeatureResponse)
    async def extract_feature(image: UploadFile = File(...)):
        return await execute(image, extraction)

    def extraction_v2(quality_filter):
        def run(model, contents):
            pipeline = getattr(model, "v2", None)
            if pipeline is None:
                raise HTTPException(404, detail={"errorCode": "FEATURE_VERSION_DISABLED"})
            result = pipeline.extract(decoder(contents), quality_filter=quality_filter)
            body = v2_response(result, pipeline.model_versions)
            feature = body["feature"]
            if result.success and (feature is None or len(feature) != 512 or not any(feature)):
                raise HTTPException(502, detail={"errorCode": "INVALID_FEATURE"})
            return body
        return run

    @application.post("/api/v2/extract_feature")
    async def extract_feature_v2(image: UploadFile = File(...), quality_filter: bool = Form(True)):
        """arcface_r50_512 feature of the largest face, with box, landmarks, pose and quality.

        success=false with reason_code NO_FACE, FACE_TOO_SMALL, BAD_POSE, TOO_DARK, TOO_BRIGHT,
        TOO_BLURRY or FEATURE_FAILED is a normal 200 answer about the image.
        """
        return await execute(image, extraction_v2(quality_filter))

    @application.get("/api/feature_versions")
    async def feature_versions():
        model = runtime.model
        loaded = [] if model is None else [v for v, m in ((V1_VERSION, getattr(model, "v1", model)), (V2_VERSION, getattr(model, "v2", None))) if m is not None]
        return {"feature_versions": loaded}

    @application.post("/api/detect_face", response_model=DetectFaceResponse)
    async def detect_face(image: UploadFile = File(...), expand_scale: float = Form(0.0)):
        if not math.isfinite(expand_scale) or expand_scale < 0 or expand_scale > 10:
            raise HTTPException(400, detail={"errorCode": "INVALID_EXPAND_SCALE"})

        def detection(model, contents):
            started = time.monotonic()
            decoded = decoder(contents)
            height, width = decoded.shape[:2]
            boxes = model.detect_faces(decoded)
            faces = []
            for box in boxes or []:
                x1, y1, x2, y2 = float(box.x1), float(box.y1), float(box.x2), float(box.y2)
                delta_x, delta_y = (x2 - x1) * expand_scale / 2, (y2 - y1) * expand_scale / 2
                faces.append(FaceRect(x1=max(0.0, min(x1 - delta_x, width)), y1=max(0.0, min(y1 - delta_y, height)),
                                      x2=max(0.0, min(x2 + delta_x, width)), y2=max(0.0, min(y2 + delta_y, height)), score=float(box.score)))
            return DetectFaceResponse(success=bool(faces), message="Success" if faces else "No face detected",
                                      faces=faces, processing_time=time.monotonic() - started)
        return await execute(image, detection)

    return application


app = create_app()

if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="Face Feature Extraction Microservice")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=int(os.getenv("FACE_WORKERS", "1")))
    args = parser.parse_args()
    # One model/slot per process. Each worker loads its own models; the pipeline is mostly
    # single-threaded Python, so use about one worker per core with FACE_ORT_THREADS=1.
    if args.workers > 1:
        uvicorn.run("feature_server:app", host="0.0.0.0", port=args.port, workers=args.workers)
    else:
        uvicorn.run(app, host="0.0.0.0", port=args.port)
