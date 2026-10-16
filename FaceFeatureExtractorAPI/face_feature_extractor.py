# -*- coding: utf-8 -*-
"""
独立人脸特征提取模块
输入：图像 (numpy.ndarray)
输出：质量评估合格的特征值 (numpy.ndarray)

功能：
1. 人脸检测和关键点检测
2. 人脸对齐和预处理
3. 多维度质量评估
4. 特征提取和标准化
5. 质量过滤，只返回合格特征
"""

import cv2
import numpy as np
import onnxruntime as ort
import logging
from typing import List, Tuple, Optional, Dict, Any, Union
from dataclasses import dataclass
import time
import os

# 导入原有的模型类
from models.facedetector import FaceBoxesV2, Box
from models.facerecoger import FaceRecoger
from models.facelandmarks5er import Landmark5er
from models.facealign import FaceAlign
from models.ort_options import default_threads, session_options
from models.imgchecker import QualityOfClarity, QualityOfPose, QualityChecker

# 设置ONNX Runtime日志级别
ort.set_default_logger_severity(3)

logger = logging.getLogger(__name__)

@dataclass
class FaceInfo:
    """人脸信息"""
    bbox: Tuple[float, float, float, float]  # (x1, y1, x2, y2)
    landmarks: List[Tuple[float, float]]     # 5个关键点
    confidence: float                         # 检测置信度
    quality_scores: Dict[str, Any]           # 质量评分
    feature: Optional[np.ndarray] = None     # 特征向量

@dataclass
class FeatureExtractionResult:
    """特征提取结果"""
    success: bool
    faces: List[FaceInfo]
    processing_time: float
    error_message: Optional[str] = None

class FaceFeatureExtractor:
    """独立人脸特征提取器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化特征提取器

        Args:
            config: 配置字典，如果为None则使用默认配置
        """
        self.config = config or self._get_default_config()
        self._init_models()
        self._init_quality_checkers()

        # 统计信息
        self.stats = {
            'total_extractions': 0,
            'successful_extractions': 0,
            'quality_filtered': 0,
            'average_processing_time': 0.0
        }

    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            'model_paths': {
                'detector': './checkpoints/faceboxesv2-640x640.onnx',
                'landmk1': './checkpoints/face_landmarker_pts5_net1.onnx',
                'landmk2': './checkpoints/face_landmarker_pts5_net2.onnx',
                'recognizer': './checkpoints/face_recognizer.onnx',
                'rotifier': './checkpoints/model_gray_mobilenetv2_rotcls.onnx',
                'num_threads': default_threads(4)
            },
            'detection': {
                'score_threshold': 0.35,
                'iou_threshold': 0.45,
                'max_faces': 1  # 最多处理的人脸数量
            },
            'quality': {
                'brightness': {
                    'v0': 69.0, 'v1': 70.0, 'v2': 230.0, 'v3': 231.0
                },
                'resolution': {
                    'height': 112, 'width': 112
                },
                'clarity': {
                    'low_thrd': 0.10, 'high_thrd': 0.20
                },
                'pose': {
                    'yaw_thrd': 30.0, 'pitch_thrd': 25.0,
                    'var_onnx_path': './checkpoints/fsanet-var.onnx',
                    'conv_onnx_path': './checkpoints/fsanet-conv.onnx'
                },
                'strict_mode': True  # 严格模式，所有质量检查都通过才返回特征
            }
        }

    def _init_models(self):
        """初始化模型"""
        try:
            # 人脸检测器
            self.face_detector = FaceBoxesV2(
                self.config['model_paths']['detector'],
                num_threads=self.config['model_paths']['num_threads']
            )

            # 关键点检测器
            self.landmark_detector = Landmark5er(
                self.config['model_paths']['landmk1'],
                self.config['model_paths']['landmk2'],
                num_threads=self.config['model_paths']['num_threads']
            )

            # 人脸对齐
            self.face_aligner = FaceAlign()

            # 特征提取器
            self.feature_extractor = FaceRecoger(
                self.config['model_paths']['recognizer'],
                num_threads=self.config['model_paths']['num_threads']
            )

            # 旋转分类器：只建一次会话。原实现每次请求都新建 InferenceSession，占单次请求近一半耗时。
            rotifier_path = self.config['model_paths']['rotifier']
            self.rotation_session = None
            if os.path.exists(rotifier_path):
                options = session_options(self.config['model_paths']['num_threads'])
                self.rotation_session = ort.InferenceSession(rotifier_path, sess_options=options)
                self.rotation_input_name = self.rotation_session.get_inputs()[0].name

            logger.info("所有模型初始化成功")
        except Exception as e:
            logger.error(f"模型初始化失败: {e}")
            raise

    def _init_quality_checkers(self):
        """初始化质量检查器"""
        try:
            # 亮度检查器
            self.brightness_checker = QualityChecker(
                self.config['quality']['brightness']['v0'],
                self.config['quality']['brightness']['v1'],
                self.config['quality']['brightness']['v2'],
                self.config['quality']['brightness']['v3'],
                hw=(self.config['quality']['resolution']['height'],
                   self.config['quality']['resolution']['width'])
            )

            # 姿态检查器
            self.pose_checker = QualityOfPose(
                yaw_thrd=self.config['quality']['pose']['yaw_thrd'],
                pitch_thrd=self.config['quality']['pose']['pitch_thrd'],
                var_onnx_path=self.config['quality']['pose']['var_onnx_path'],
                conv_onnx_path=self.config['quality']['pose']['conv_onnx_path'],
                num_threads=self.config['model_paths']['num_threads']
            )

            # 清晰度检查器
            self.clarity_checker = QualityOfClarity(
                low_thresh=self.config['quality']['clarity']['low_thrd'],
                high_thresh=self.config['quality']['clarity']['high_thrd']
            )

            logger.info("质量检查器初始化成功")
        except Exception as e:
            logger.error(f"质量检查器初始化失败: {e}")
            raise

    def correct_image_rotation(self, image: np.ndarray) -> np.ndarray:
        """图像旋转校正"""
        try:
            if self.rotation_session is None:
                return image

            # 转换为灰度图像
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (256, 256))

            # 中心裁剪到 224x224
            start = (256 - 224) // 2
            gray = gray[start:start + 224, start:start + 224]

            # 转换为三通道
            gray_3d = np.stack([gray] * 3, axis=-1)

            # 归一化
            gray_3d = gray_3d.astype(np.float32) / 255.0
            gray_3d = gray_3d.transpose(2, 0, 1)
            gray_3d = np.expand_dims(gray_3d, axis=0)

            # 旋转检测
            outputs = self.rotation_session.run(None, {self.rotation_input_name: gray_3d})
            label = np.argmax(outputs[0][0])

            # 应用旋转
            if label == 1:
                corrected = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
                logger.debug("图像顺时针旋转90度")
            elif label == 2:
                corrected = cv2.rotate(image, cv2.ROTATE_180)
                logger.debug("图像旋转180度")
            elif label == 3:
                corrected = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
                logger.debug("图像逆时针旋转90度")
            else:
                corrected = image

            return corrected
        except Exception as e:
            logger.warning(f"图像旋转校正失败，使用原图: {e}")
            return image

    def detect_faces(self, image: np.ndarray) -> List[Box]:
        """人脸检测"""
        try:
            boxes = self.face_detector.detect(
                image,
                score_threshold=self.config['detection']['score_threshold'],
                iou_threshold=self.config['detection']['iou_threshold'],
                topk=self.config['detection']['max_faces']
            )
            return boxes
        except Exception as e:
            logger.error(f"人脸检测失败: {e}")
            return []

    def extract_landmarks(self, image: np.ndarray, boxes: List[Box]) -> List[List[Tuple[float, float]]]:
        """提取关键点"""
        landmarks_list = []
        for box in boxes:
            try:
                box_coords = (box.x1, box.y1, box.x2, box.y2)
                landmarks = self.landmark_detector.inference(image, box_coords)
                landmarks_list.append(landmarks)
            except Exception as e:
                logger.error(f"关键点检测失败: {e}")
                landmarks_list.append([])
        return landmarks_list

    def align_face(self, image: np.ndarray, landmarks: List[Tuple[float, float]]) -> np.ndarray:
        """人脸对齐"""
        try:
            if not landmarks:
                return None

            landmarks_2d = [[ld[0], ld[1]] for ld in landmarks]
            aligned_face = self.face_aligner.align(image, landmarks_2d)
            return aligned_face
        except Exception as e:
            logger.error(f"人脸对齐失败: {e}")
            return None

    def extract_feature(self, aligned_face: np.ndarray) -> np.ndarray:
        """提取特征"""
        try:
            feature = self.feature_extractor.inference(aligned_face)
            return feature
        except Exception as e:
            logger.error(f"特征提取失败: {e}")
            return None

    def _preprocess_small_face(self, face_region: np.ndarray,
                               min_width: int = 112,
                               min_height: int = 112,
                               apply_sharpening: bool = True) -> np.ndarray:
        """
        智能预处理小尺寸人脸图像

        Args:
            face_region: 原始人脸区域
            min_width: 最小宽度要求
            min_height: 最小高度要求
            apply_sharpening: 是否应用锐化增强

        Returns:
            处理后的人脸区域
        """
        h, w = face_region.shape[:2]

        # 检查是否需要放大
        if h >= min_height and w >= min_width:
            return face_region

        # 计算缩放比例(保证两个维度都满足要求)
        scale_w = min_width / w if w < min_width else 1.0
        scale_h = min_height / h if h < min_height else 1.0
        scale = max(scale_w, scale_h)

        # 计算新尺寸
        new_width = int(w * scale)
        new_height = int(h * scale)

        # 使用双三次插值放大(质量最好)
        resized = cv2.resize(face_region, (new_width, new_height),
                            interpolation=cv2.INTER_CUBIC)

        # 可选:应用USM锐化增强放大后的图像
        if apply_sharpening:
            # 创建高斯模糊版本
            blurred = cv2.GaussianBlur(resized, (0, 0), 2.0)
            # USM锐化: 原图 + 锐化强度 * (原图 - 模糊图)
            sharpened = cv2.addWeighted(resized, 1.5, blurred, -0.5, 0)
            resized = sharpened

        logger.debug(f"小尺寸人脸预处理: ({w}x{h}) -> ({new_width}x{new_height}), "
                    f"缩放比例: {scale:.2f}, 锐化: {apply_sharpening}")

        return resized

    def assess_quality(self, image: np.ndarray, box: Box, aligned_face: np.ndarray) -> Dict[str, Any]:
        """质量评估"""
        quality_scores = {}

        try:
            # 裁剪人脸区域
            x1, y1, x2, y2 = int(box.x1), int(box.y1), int(box.x2), int(box.y2)
            face_region = image[y1:y2, x1:x2]

            # 智能预处理:如果人脸区域小于要求,自动放大并增强
            min_width = self.config['quality']['resolution']['width']
            min_height = self.config['quality']['resolution']['height']
            face_region = self._preprocess_small_face(
                face_region,
                min_width=min_width,
                min_height=min_height,
                apply_sharpening=True
            )

            # 亮度检查
            quality_scores['brightness'] = {
                'passed': self.brightness_checker.check_bright(face_region),
                'description': '亮度检查'
            }

            # 分辨率检查(现在应该能通过了)
            quality_scores['resolution'] = {
                'passed': self.brightness_checker.check_resolution(face_region),
                'description': '分辨率检查'
            }

            # 清晰度检查
            quality_scores['clarity'] = {
                'passed': self.clarity_checker.check(face_region),
                'description': '清晰度检查'
            }

            # 姿态检查
            pose_result = self.pose_checker.check(aligned_face)
            quality_scores['pose'] = {
                'passed': pose_result == 'frontFace',
                'description': '姿态检查',
                'pose_result': pose_result
            }

            # 整体质量评估
            all_passed = all(score['passed'] for score in quality_scores.values())
            quality_scores['overall'] = {
                'passed': all_passed,
                'description': '整体质量评估'
            }

        except Exception as e:
            logger.error(f"质量评估失败: {e}")
            # 设置默认值
            quality_scores = {
                'brightness': {'passed': False, 'description': '亮度检查失败'},
                'resolution': {'passed': False, 'description': '分辨率检查失败'},
                'clarity': {'passed': False, 'description': '清晰度检查失败'},
                'pose': {'passed': False, 'description': '姿态检查失败'},
                'overall': {'passed': False, 'description': '整体质量评估失败'}
            }

        return quality_scores

    def extract_features(self, image: np.ndarray,
                        return_all_faces: bool = False,
                        quality_filter: bool = True) -> FeatureExtractionResult:
        """
        提取人脸特征（主接口）

        Args:
            image: 输入图像 (BGR格式)
            return_all_faces: 是否返回所有人脸，还是只返回质量合格的
            quality_filter: 是否进行质量过滤

        Returns:
            FeatureExtractionResult: 特征提取结果
        """
        start_time = time.time()
        self.stats['total_extractions'] += 1

        try:
            # 图像旋转校正
            corrected_image = self.correct_image_rotation(image)

            # 人脸检测
            boxes = self.detect_faces(corrected_image)
            if not boxes:
                return FeatureExtractionResult(
                    success=False,
                    faces=[],
                    processing_time=time.time() - start_time,
                    error_message="未检测到人脸"
                )

            # 限制处理的人脸数量
            max_faces = self.config['detection']['max_faces']
            if max_faces > 0:
                boxes = boxes[:max_faces]

            # 关键点检测
            landmarks_list = self.extract_landmarks(corrected_image, boxes)

            # 处理每个人脸
            face_infos = []
            for i, (box, landmarks) in enumerate(zip(boxes, landmarks_list)):
                try:
                    # 人脸对齐
                    aligned_face = self.align_face(corrected_image, landmarks)
                    if aligned_face is None:
                        logger.warning(f"人脸 {i+1} 对齐失败")
                        continue

                    # 质量评估先于特征提取：被过滤的人脸不再跑识别模型（单次推理最贵的一步）
                    quality_scores = self.assess_quality(corrected_image, box, aligned_face)

                    # 质量过滤
                    if quality_filter and self.config['quality']['strict_mode']:
                        if not quality_scores['overall']['passed']:
                            self.stats['quality_filtered'] += 1
                            logger.debug(f"人脸 {i+1} 质量检查未通过")
                            if not return_all_faces:
                                continue

                    # 特征提取
                    feature = self.extract_feature(aligned_face)
                    if feature is None:
                        logger.warning(f"人脸 {i+1} 特征提取失败")
                        continue

                    face_infos.append(FaceInfo(
                        bbox=(box.x1, box.y1, box.x2, box.y2),
                        landmarks=landmarks,
                        confidence=box.score,
                        quality_scores=quality_scores,
                        feature=feature
                    ))

                except Exception as e:
                    logger.error(f"处理人脸 {i+1} 时出错: {e}")
                    continue

            processing_time = time.time() - start_time

            # 更新统计信息
            if face_infos:
                self.stats['successful_extractions'] += 1

            self.stats['average_processing_time'] = (
                (self.stats['average_processing_time'] * (self.stats['total_extractions'] - 1) +
                 processing_time) / self.stats['total_extractions']
            )

            # Check if we detected faces but filtered them all out due to quality
            if not face_infos and boxes:
                return FeatureExtractionResult(
                    success=False,
                    faces=[],
                    processing_time=processing_time,
                    error_message="Face quality check failed (brightness/clarity/pose)"
                )

            return FeatureExtractionResult(
                success=len(face_infos) > 0,
                faces=face_infos,
                processing_time=processing_time
            )

        except Exception as e:
            logger.error(f"特征提取失败: {e}")
            return FeatureExtractionResult(
                success=False,
                faces=[],
                processing_time=time.time() - start_time,
                error_message=str(e)
            )

    def extract_single_feature(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        提取单个人脸特征（简化接口）

        Args:
            image: 输入图像

        Returns:
            Optional[np.ndarray]: 质量合格的特征向量，如果质量不合格则返回None
        """
        result = self.extract_features(image, return_all_faces=False, quality_filter=True)

        if result.success and result.faces:
            return result.faces[0].feature
        else:
            return None

    def extract_multiple_features(self, image: np.ndarray) -> List[np.ndarray]:
        """
        提取多个人脸特征（简化接口）

        Args:
            image: 输入图像

        Returns:
            List[np.ndarray]: 质量合格的特征向量列表
        """
        result = self.extract_features(image, return_all_faces=True, quality_filter=True)

        if result.success:
            return [face.feature for face in result.faces if face.feature is not None]
        else:
            return []

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'total_extractions': self.stats['total_extractions'],
            'successful_extractions': self.stats['successful_extractions'],
            'quality_filtered': self.stats['quality_filtered'],
            'success_rate': self.stats['successful_extractions'] / max(self.stats['total_extractions'], 1),
            'quality_pass_rate': (self.stats['successful_extractions'] - self.stats['quality_filtered']) / max(self.stats['successful_extractions'], 1),
            'average_processing_time': self.stats['average_processing_time']
        }

# 便捷函数
def extract_face_feature(image: np.ndarray, config: Optional[Dict] = None) -> Optional[np.ndarray]:
    """
    便捷函数：提取单个人脸特征

    Args:
        image: 输入图像
        config: 可选配置

    Returns:
        Optional[np.ndarray]: 特征向量
    """
    extractor = FaceFeatureExtractor(config)
    return extractor.extract_single_feature(image)

def extract_face_features(image: np.ndarray, config: Optional[Dict] = None) -> List[np.ndarray]:
    """
    便捷函数：提取多个人脸特征

    Args:
        image: 输入图像
        config: 可选配置

    Returns:
        List[np.ndarray]: 特征向量列表
    """
    extractor = FaceFeatureExtractor(config)
    return extractor.extract_multiple_features(image)

if __name__ == "__main__":
    # 示例使用
    import sys

    if len(sys.argv) < 2:
        print("用法: python face_feature_extractor.py <image_path>")
        sys.exit(1)

    image_path = sys.argv[1]

    try:
        # 读取图像
        image = cv2.imread(image_path)
        if image is None:
            print(f"无法读取图像: {image_path}")
            sys.exit(1)

        # 创建特征提取器
        extractor = FaceFeatureExtractor()

        # 提取特征
        result = extractor.extract_features(image)
        #保存文件
        with open('feature.txt', 'w') as f:
            f.write(str(result.faces[0].feature.tolist()))

        # 输出结果
        print(result.faces[0].feature)
        print(f"处理时间: {result.processing_time:.3f}秒")
        print(f"检测到 {len(result.faces)} 个人脸")

        for i, face in enumerate(result.faces):
            print(f"\n人脸 {i+1}:")
            print(f"  位置: ({face.bbox[0]:.1f}, {face.bbox[1]:.1f}, {face.bbox[2]:.1f}, {face.bbox[3]:.1f})")
            print(f"  置信度: {face.confidence:.3f}")
            print(f"  特征维度: {face.feature.shape if face.feature is not None else 'None'}")

            print("  质量评估:")
            for name, score in face.quality_scores.items():
                status = "✓" if score['passed'] else "✗"
                print(f"    {status} {score['description']}")

            if face.feature is not None:
                print(f"  特征向量范数: {np.linalg.norm(face.feature):.6f}")

        # 统计信息
        stats = extractor.get_statistics()
        print(f"\n统计信息:")
        for key, value in stats.items():
            print(f"  {key}: {value}")

    except Exception as e:
        print(f"处理失败: {e}")
        import traceback
        traceback.print_exc()