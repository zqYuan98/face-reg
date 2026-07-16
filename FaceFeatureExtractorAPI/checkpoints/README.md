# 模型文件目录

此目录用于存放ONNX模型文件。

## 需要的模型文件

请将以下模型文件放置到此目录：

1. **faceboxesv2-640x640.onnx** (约30MB)
   - 人脸检测模型
   - 基于FaceBoxes架构

2. **face_landmarker_pts5_net1.onnx** (约1MB)
   - 人脸关键点检测模型（网络1）
   - 检测5个关键点：双眼、鼻尖、嘴角

3. **face_landmarker_pts5_net2.onnx** (约1MB)
   - 人脸关键点检测模型（网络2）
   - 用于精细化关键点定位

4. **face_recognizer.onnx** (约100MB)
   - 人脸特征提取模型
   - 输出512维特征向量

5. **model_gray_mobilenetv2_rotcls.onnx** (约10MB)
   - 图像旋转角度分类模型
   - 检测0°/90°/180°/270°旋转

6. **fsanet-var.onnx** (约2MB)
   - 人脸姿态估计模型VAR
   - 估计yaw和pitch角度

7. **fsanet-conv.onnx** (约2MB)
   - 人脸姿态估计模型CONV
   - 用于姿态估计

## 模型来源

这些模型文件需要从原始FaceRegWeb项目的checkpoints目录复制。

如果没有这些文件，请联系项目维护者或参考原项目获取。

## 文件大小参考

总计约150MB左右的模型文件。

## 验证模型

确保所有模型文件都已正确放置后，可以运行以下命令验证：

```bash
python -c "import os; files=['faceboxesv2-640x640.onnx','face_landmarker_pts5_net1.onnx','face_landmarker_pts5_net2.onnx','face_recognizer.onnx','model_gray_mobilenetv2_rotcls.onnx','fsanet-var.onnx','fsanet-conv.onnx']; missing=[f for f in files if not os.path.exists(f'checkpoints/{f}')]; print('缺失文件:', missing) if missing else print('所有模型文件已就绪')"
```
