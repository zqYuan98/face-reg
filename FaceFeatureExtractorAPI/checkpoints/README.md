# 人脸算法模型文件

仓库已包含此目录下的七个 ONNX 文件：

| 文件 | 用途 |
| --- | --- |
| `faceboxesv2-640x640.onnx` | 人脸检测 |
| `face_landmarker_pts5_net1.onnx` | 五点关键点检测 |
| `face_landmarker_pts5_net2.onnx` | 五点关键点精定位 |
| `face_recognizer.onnx` | 人脸特征提取 |
| `model_gray_mobilenetv2_rotcls.onnx` | 图像旋转分类 |
| `fsanet-var.onnx` | 姿态估计 |
| `fsanet-conv.onnx` | 姿态估计 |

当前 HTTP `/api/extract_feature` 输出的 `feature` 为 **1024 维**，版本标识 `recognizer_v1_1024`。服务 1.4.0 起另有 `/api/v2/extract_feature`，输出 512 维 `arcface_r50_512`，所用 InsightFace 权重（仅限非商业研究用途）不入库，放在本目录下被 git 忽略的 `insightface/` 子目录，见 [FEATURE_V2.md](../FEATURE_V2.md)。`/api/compare_feature` 和 `/api/search_feature` 要求全部特征为同一版本、维度与版本一致。接口字段以 [特征比对与检索说明](../FEATURE_COMPARISON.md) 为准，不根据模型文件名推断接口维度。

现场部署须核对实际模型文件与已验收版本一致。37 服务器通过只读挂载提供 `models` 和 `checkpoints`；新机器可从本仓库取得文件，再确认挂载路径、算法 `/ready` 和既有图片的检测、提取结果。部署版本及镜像要求见 [实施交接单](../../docs/DEPLOYMENT_HANDOFF_2026-10-14.md)。
