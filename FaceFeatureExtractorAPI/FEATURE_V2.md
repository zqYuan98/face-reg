# 特征版本 arcface_r50_512 与 CPU / GPU 两个版本

## 模型与许可

| 项 | 内容 |
|---|---|
| 特征版本 | `arcface_r50_512`，512 维，L2 归一化，余弦（点积）比较 |
| 流水线 | SCRFD 检测（5 个关键点）→ ArcFace 模板相似变换对齐到 112×112 → IResNet-50（w600k_r50） |
| 代码 | [models/arcface_pipeline.py](models/arcface_pipeline.py)，与 `tools/engine-eval/insightface_ref.py` 及安卓端 `libfaceengine` 同一实现，120 张 LFW 图余弦全部为 1.000000 |
| 权重 | InsightFace 预训练权重，**仅限非商业研究用途**；不入库、不打进镜像，运行时放到 `checkpoints/insightface/`（`FACE_V2_MODEL_DIR`）。需要的文件：`det_500m.onnx`、`det_10g.onnx`、`w600k_r50.onnx`、`w600k_r50_int8.onnx` |

与 `recognizer_v1_1024` 并存，互不影响：v1 的接口、特征和同步格式都不变。两个版本的特征不能互相比较，比对接口会拒绝混用。

## 两个部署版本

| | CPU 版（`Dockerfile`） | GPU 版（`Dockerfile.gpu`） |
|---|---|---|
| 运行时 | onnxruntime（CPU） | onnxruntime-gpu，CUDA 12 / cuDNN 由 wheel 提供，宿主机只需驱动和 nvidia-container-toolkit |
| 检测器 | det_500m | det_10g（复杂场景召回更好） |
| 识别模型 | w600k_r50_int8 | w600k_r50（fp32） |
| 翻转增强 | 关 | 开 |
| 默认 | `FACE_WORKERS=2`、`FACE_ORT_THREADS=1` | `FACE_WORKERS=2`、`FACE_ORT_THREADS=2` |
| 启动 | `docker compose up -d` | `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d` |

两个版本产出的是**同一个特征空间**，可以混用：CPU 版注册、GPU 版检索都没问题，设备端 int8 同样可以。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `FACE_FEATURE_VERSIONS` | `recognizer_v1_1024` | 本进程加载的版本，逗号分隔；加上 `arcface_r50_512` 才会开启 v2 |
| `FACE_DEVICE` | `cpu` | `cpu`、`cuda` 或 `tensorrt`；指定 GPU 但当前运行时不支持时启动报错，不会悄悄退回 CPU |
| `FACE_DEVICE_ID` | 0 | GPU 编号 |
| `FACE_V2_MODEL_DIR` | `./checkpoints/insightface` | 权重目录 |
| `FACE_V2_DETECTOR` / `FACE_V2_RECOGNIZER` | 按设备取上表默认值 | 覆盖模型文件 |
| `FACE_V2_FLIP` | CPU 为 0，GPU 为 1 | 翻转增强 |
| `FACE_V2_MIN_FACE` | 64 | 人脸框短边下限（像素） |
| `FACE_WORKERS`、`FACE_ORT_THREADS`、`FACE_INFERENCE_QUEUE` | 见 [BOUNDED_EXECUTION.md](BOUNDED_EXECUTION.md) | 进程数、每个会话的线程数、排队上限 |

使用 `tensorrt` 时需要另外安装 `tensorrt-cu12`，引擎缓存写到 `<模型目录>/trt_cache`，首次启动要构建数分钟。这条路径未实测。

## 接口

`POST /api/v2/extract_feature`，multipart 字段 `image`，可选字段 `quality_filter`（默认 true）。返回检测到的最大人脸的特征：

```json
{"success": true, "reason_code": null, "feature_version": "arcface_r50_512",
 "feature": [512 个数], "feature_dim": 512,
 "face": {"box": {"x1":0,"y1":0,"x2":0,"y2":0}, "score": 0.88, "landmarks": [[x,y] x5],
          "pose": {"yaw":0,"pitch":0,"roll":0},
          "quality": {"size":{}, "pose":{}, "brightness":{}, "clarity":{}, "cropped":{}}},
 "face_count": 1, "rotation": 0, "model_versions": {}, "processing_time_ms": 14.2}
```

- **质量不合格**：仍返回 HTTP 200，`success=false`，并给出 `reason_code`，取值为 `NO_FACE`、`FACE_TOO_SMALL`、`BAD_POSE`（|yaw|>30° 或 |pitch|>25°，与设备端同一套 5 点公式）、`TOO_DARK`、`TOO_BRIGHT`、`TOO_BLURRY`、`FEATURE_FAILED`。
- **方向**：解码时已按 EXIF 方向转正；如果仍检测不到人脸，依次尝试旋转 90°、270°、180°，`rotation` 字段返回实际转过的角度。
- **未加载 v2**：请求返回 404 `FEATURE_VERSION_DISABLED`。`GET /api/feature_versions` 列出本进程已加载的版本。

`POST /api/compare_feature` 现在同时接受两个版本，但两侧必须是同一版本、且维度与版本一致。

## 实测（2026-10-16，AMD Ryzen 7 9700X 8 核 / RTX 5070）

**识别率**：LFW 全配对（约 3000 万对冒名者），取画面中心的脸。

| 配置 | TAR@FAR=1e-4 | TAR@1e-5 | 1e-5 阈值 | TAR@1e-6 |
|---|---|---|---|---|
| v1 recognizer_v1_1024（对照） | 95.74% | 88.32% | 0.700 | 74.21% |
| v2 CPU 版（det_500m + r50 int8） | 99.90% | 99.86% | 0.334 | 85.32% |
| v2 GPU 版（det_10g + r50 + 翻转增强） | 99.90% | 99.87% | 0.336 | 85.90% |
| 服务端 fp32 注册 × 设备端 int8 识别 | 99.74% | 99.57% | 0.312 | 86.89% |

最后一行用的是另一套缓存特征（det_10g，不做翻转增强），说明服务端和设备端的特征可以互通。LFW 已经饱和，正式阈值要用自有数据按 FAR 校准。

**速度**：压测客户端与服务在同一台机器上。

| 配置 | 单张 P50 | 吞吐 |
|---|---|---|
| CPU 版，1 进程 × 4 线程（Linux） | 27 ms | 37 张/秒 |
| CPU 版，8 进程（Linux） | — | 58 张/秒；同条件 fp32 为 25 张/秒 |
| GPU 版，1 进程 | 14 ms | 68 张/秒 |
| GPU 版，4 进程，16 并发 | — | 105 张/秒，GPU 利用率约 70%，瓶颈在 CPU 端的解码和前处理 |

单模型耗时（GPU）：r50 批量 1 为 3.1 ms，批量 32 折合每张 0.68 ms；det_10g 为 4.6 ms。
