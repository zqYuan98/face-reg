# 自研引擎评测脚本（engine-eval）

验证 `android-sdk/face-core/src/main/cpp/engine`（`libfaceengine.so`）与 Python 注册服务一致、量化模型可用、各项改动对识别率的影响。只用于内部评测，结论见[设计文档](../../docs/superpowers/specs/2026-10-12-offline-face-engine-design.md)第 7 节"进展（2026-10-13，阶段 A）"。

## 环境

所有脚本都在 `FaceFeatureExtractorAPI/` 目录下运行，中间文件写到它的 `tmp/`（已被 git 忽略）：

```bash
cd FaceFeatureExtractorAPI
python -m venv .venv && .venv/Scripts/python -m pip install numpy opencv-python-headless onnxruntime onnx ncnn
```

设备端程序 `face_engine_cli` 用 NDK 交叉编译，源码在 `android-sdk/face-core/src/main/cpp/engine/cli/`：

```bash
SDK=<Android SDK>; CM=$SDK/cmake/3.22.1/bin
$CM/cmake -S android-sdk/face-core/src/main/cpp/engine/cli -B <build> -G Ninja -DCMAKE_MAKE_PROGRAM=$CM/ninja \
  -DCMAKE_TOOLCHAIN_FILE=$SDK/ndk/25.1.8937393/build/cmake/android.toolchain.cmake -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 -DANDROID_STL=c++_shared
$CM/cmake --build <build>
```

把程序、`jniLibs/arm64-v8a` 里的 `libonnxruntime.so libopencv_{core,imgproc,imgcodecs}.so libc++_shared.so`、模型和图片推到 `/data/local/tmp/fe/`，用 `LD_LIBRARY_PATH=. ./face_engine_cli ...` 运行。

## 脚本

| 脚本 | 作用 |
|---|---|
| `make_golden.py <图片glob> <数量>` | 生成无损 PNG 输入和 Python 服务的逐步输出（框、关键点、对齐图校验和、特征） |
| `compare_golden.py <python输出> <设备输出>` | 比较设备端 `face_engine_cli golden` 与 Python 的结果 |
| `quantize.py <校准图片glob>` | 生成 int8 识别模型（只量化卷积，首层保持 fp32，Percentile 校准）。产物即 `face_recognizer_int8.onnx` |
| `eval_quant.py` | int8 与 fp32 特征的余弦 |
| `lfw_eval.py <LFW目录> [int8模型]` | LFW 6000 对：截断 / 亚像素 / TTA 关键点 × fp32 / int8，以及注册端与识别端混用 |
| `lfw_far.py` | 用 `lfw_eval.py` 缓存的特征做全配对（约 3000 万异人对），给出 TAR@FAR=1e-3/1e-4/1e-5 |
| `pose_fit.py` | 5 点几何姿态对 FSANet 的线性拟合，系数写在 `Pose5.java` |
| `box_experiment.py <图片glob> <数量>` | 跟踪器框与 FaceBoxes 框喂给关键点网络时特征的差异 |

LFW 只能用于内部评测；测试图片（HardHat 等）和 LFW 都不进仓库。
