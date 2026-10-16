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

## 会话回放结果核对

```bash
python tools/engine-eval/session_compare.py <运行目录> --subject xxr
python tools/engine-eval/session_compare.py <运行目录> --subject xxr --json <输出.json>
```

只读取 `session_*` 实测行，不再把普通 `arcsoft` 的逐帧结果模拟成会话时延。需要跨运行读取虹软时，可用 `--arcsoft-run <对照目录>`，但该目录必须有 `session_arcsoft`，且片段身份、标签、帧索引和时间戳必须相同。

- 真人正确识别必须核对 `recognized_id == subject_id`；错认、未决、缺数据、错误分别计数。
- 攻击区分认证放行、仅活体通过、观察到非活体、未决/无脸/错误。没有识别事件不能等同于活体拒绝。
- 决策时延从回放首帧起算，仅对正确识别的片段求中位数。schema v2 使用独立 `events.csv` 和 `clip-completion.json`，在片尾有限等待已提交任务并导出尾事件，不重复末帧或补造活体票。旧版 CSV 仍可能漏掉末帧后的异步决策，只能作为观察结果。
- `session_*` 的 `detect_ms` 是分析线程 `onFrame` 调用成本，包含空闲短路和预热调用，不包含后台推理。其余计时列是子步骤，不能再次相加后宣称为完整流水线耗时。
- 该报告不计算或证明 FAR；少量片段只用于冒烟测试和定位差距。
- v2 的完整性审计要求输入/注册照/模型/APK指纹、实际入库集合、预期后端与片段覆盖、无重复/乱序帧、有效会话配置及片尾完成状态；错误、取消、超时或事件溢出使严格验收不通过，但原始结果仍保留供分析。

### 安全基线与共享裁剪候选

台架启动时的 `evaluationVariant` intent extra 只接受 `baseline`（默认）或 `shared_capture`。两者模型和阈值相同，共享候选只开启 `SessionConfig.shareFeatureCapture`。运行目录记录该开关以及每片 `featureCaptureCount`、`knownPreparedBytes`。跨run比较必须先各自通过审计，并核对同一APK、模型、输入及注册集，不能用旧版缺少完成性证据的run替代新基线。

App 的候选构建开关为 `-Pface.shareCapture=true` 和 `-Pface.earlyAdmission=true`，默认均关闭。App 默认不将本地同步设备凭据编入APK；本轮测试不修改本地凭据文件或服务器配置。

回归测试（在仓库根目录）：

```bash
python -m pytest tools/engine-eval/tests tools/face-eval/tests
```

## NNAPI 试验

设备 CLI 的 `benchmodel <模型> <N> <C> <H> <W> <次数> <线程数> [nnapi_flags=-1] [profile_prefix]` 接受 NNAPI flags，默认 `-1` 仍为 CPU。输出包含请求的 flags、加载时延和5次预热后时延；可选profile用于核对provider分配，开启profile会影响计时，应单独运行。**请求 NNAPI 不代表实际使用 NPU**。`CPU_DISABLED` 仅禁止 NNAPI 自己的 CPU 设备，ORT 仍可回退到 CPU。需要结合 provider 分配及设备日志确认，并验证相同输入下的输出差异，再考虑接入 App。
