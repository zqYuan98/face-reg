# -*- coding: utf-8 -*-
"""Feature API: bounded model execution with independent liveness and readiness."""
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


def default_factory():
    # Heavy native imports and model loading run in the bounded worker, never on the event loop.
    from face_feature_extractor import FaceFeatureExtractor
    return FaceFeatureExtractor()


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
               unhealthy_after_seconds=None, max_image_bytes=None):
    runtime = BoundedInference(
        factory,
        timeout_seconds=float(os.getenv("FACE_INFERENCE_TIMEOUT_SECONDS", "12")) if timeout_seconds is None else timeout_seconds,
        unhealthy_after_seconds=float(os.getenv("FACE_INFERENCE_UNHEALTHY_SECONDS", "30")) if unhealthy_after_seconds is None else unhealthy_after_seconds,
        max_image_bytes=int(os.getenv("FACE_MAX_IMAGE_BYTES", str(10 * 1024 * 1024))) if max_image_bytes is None else max_image_bytes,
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
    args = parser.parse_args()
    # One model/slot per process. Raising worker count multiplies model memory and native concurrency.
    uvicorn.run(app, host="0.0.0.0", port=args.port)
