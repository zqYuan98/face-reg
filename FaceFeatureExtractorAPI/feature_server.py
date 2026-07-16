# -*- coding: utf-8 -*-
"""
人脸特征提取微服务
仅提供特征提取 API，供 Java 后端调用
"""

import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import numpy as np
import cv2
import logging
from face_feature_extractor import FaceFeatureExtractor

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("FeatureServer")

# 创建 FastAPI 应用
app = FastAPI(
    title="Face Feature Extraction Microservice",
    description="Dedicated service for extracting face features",
    version="1.0.0"
)

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局特征提取器实例
extractor = None

def get_extractor():
    """懒加载特征提取器"""
    global extractor
    if extractor is None:
        logger.info("Initializing FaceFeatureExtractor...")
        extractor = FaceFeatureExtractor()
        logger.info("FaceFeatureExtractor initialized.")
    return extractor

# 响应模型
class ExtractFeatureResponse(BaseModel):
    success: bool
    message: str
    feature: Optional[List[float]] = None
    feature_dim: Optional[int] = None
    processing_time: Optional[float] = None

def decode_image(file: UploadFile) -> Optional[np.ndarray]:
    """解码上传的图片"""
    try:
        contents = file.file.read()
        nparr = np.frombuffer(contents, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return image
    except Exception as e:
        logger.error(f"Image decode failed: {e}")
        return None

@app.on_event("startup")
async def startup_event():
    """启动时预加载模型"""
    get_extractor()

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Face Feature Extractor"}

@app.post("/api/extract_feature", response_model=ExtractFeatureResponse)
async def extract_feature(image: UploadFile = File(...)):
    """
    特征提取接口
    输入: 图片文件
    输出: 1024维特征向量
    """
    try:
        img = decode_image(image)
        if img is None:
            raise HTTPException(status_code=400, detail="Invalid image file")

        ext = get_extractor()
        # 调用提取器
        result = ext.extract_features(img, return_all_faces=False, quality_filter=True)

        if not result.success or not result.faces:
            return ExtractFeatureResponse(
                success=False,
                message=result.error_message or "No face detected",
                processing_time=result.processing_time
            )

        # 获取特征
        feature_vector = result.faces[0].feature.tolist()

        return ExtractFeatureResponse(
            success=True,
            message="Success",
            feature=feature_vector,
            feature_dim=len(feature_vector),
            processing_time=result.processing_time
        )

    except Exception as e:
        logger.error(f"Extraction failed: {e}", exc_info=True)
        return ExtractFeatureResponse(
            success=False,
            message=f"Server error: {str(e)}"
        )

# 新增人脸检测响应模型
class FaceRect(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float
    score: float

class DetectFaceResponse(BaseModel):
    success: bool
    message: str
    faces: List[FaceRect] = []
    processing_time: Optional[float] = None

@app.post("/api/detect_face", response_model=DetectFaceResponse)
async def detect_face(image: UploadFile = File(...), expand_scale: float = Form(0.0)):
    """
    人脸检测接口
    输入: 图片文件, 扩充比例(expand_scale)
    输出: 人脸坐标列表 (x1, y1, x2, y2)
    """
    import time
    start_time = time.time()
    try:
        img = decode_image(image)
        if img is None:
            raise HTTPException(status_code=400, detail="Invalid image file")

        # 获取图片尺寸用于坐标截断
        h_img, w_img = img.shape[:2]

        ext = get_extractor()
        # 直接调用检测器，不进行旋转校正，保证坐标对应原图
        boxes = ext.detect_faces(img)

        face_rects = []
        if boxes:
            for box in boxes:
                # 原始坐标
                x1 = float(box.x1)
                y1 = float(box.y1)
                x2 = float(box.x2)
                y2 = float(box.y2)

                # 应用扩充逻辑 (如果 expand_scale > 0)
                if expand_scale > 0:
                    w = x2 - x1
                    h = y2 - y1
                    cx = x1 + w / 2
                    cy = y1 + h / 2

                    new_w = w * (1 + expand_scale)
                    new_h = h * (1 + expand_scale)

                    x1 = cx - new_w / 2
                    y1 = cy - new_h / 2
                    x2 = cx + new_w / 2
                    y2 = cy + new_h / 2

                # 强制限制坐标在图片范围内，防止出现负数或越界
                x1 = max(0.0, min(x1, float(w_img)))
                y1 = max(0.0, min(y1, float(h_img)))
                x2 = max(0.0, min(x2, float(w_img)))
                y2 = max(0.0, min(y2, float(h_img)))

                face_rects.append(FaceRect(
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    score=float(box.score)
                ))

        return DetectFaceResponse(
            success=True if face_rects else False,
            message="Success" if face_rects else "No face detected",
            faces=face_rects,
            processing_time=time.time() - start_time
        )

    except Exception as e:
        logger.error(f"Detection failed: {e}", exc_info=True)
        return DetectFaceResponse(
            success=False,
            message=f"Server error: {str(e)}",
            processing_time=time.time() - start_time
        )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Face Feature Extraction Microservice')
    parser.add_argument('--port', type=int, default=8000, help='Service port')
    args = parser.parse_args()

    logger.info(f"Starting Feature Server on port {args.port}...")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
