# 离线人脸识别引擎设计方案

日期：2026-10-12
状态：设计草案，待评审。文中带"估算"或"待实测"的数字都不是验收结果。
依据：[虹软离线方案整理](../../../face-notes/arcsoft-offline-face-pipeline.md)、[InsightFace 算法解析](../../../face-notes/insightface-analysis.md)，以及对本仓库 `android-sdk/`、`FaceFeatureExtractorAPI/`、`backend-java/` 的代码审读。
范围：Android 离线识别 SDK 为主；Python 注册服务、Java 后端和同步协议只做配套契约变更。不改同步 v2 的核心状态机。

**本阶段目标**（2026-10-12 确认）：活体准确率、离线速度、识别率三项**在同一台设备、同一批数据上至少与虹软 ArcFace V5.0 持平**。"持平"的量化定义和测量方法见第 2 节，所有阶段的退出条件都以它为准。

**已确认的前提**（2026-10-12）：

| 项 | 结论 | 对方案的影响 |
|---|---|---|
| 目标设备 | 安卓手机；RK3568 设备 | 两类设备分别对标、分别给阈值。RK3568 只有 4 个 Cortex-A55 小核，但带 NPU，推理运行时见 3.3 |
| 红外或深度摄像头 | 没有 | 活体只能走纯 RGB 路线，L6 取消 |
| 场景 | 身份核验 | 识别主指标改为 FAR=1e-5；活体默认开启 L5 主动挑战，静默活体作为第一道关 |
| 数据采集 | 可以组织测试 | 按 2.3 节采集 |
| 虹软 SDK | 已付费，可以调用 | 对标台架直接接入，见 2.2 |

**仍按假设推进、需要确认的一点**：身份核验的比对底图是平台里登记的人员照片，比对方式是 1:1（先确定"声称是谁"，再与此人的登记照比）。如果底图是身份证芯片照片，测试集要加入芯片照，虹软一侧改用 `ID_CARD` 比对模型，台架已预留这个开关。

**一句话结论**：算法栈换成 InsightFace 的"SCRFD 检测 + 5 点相似变换 + ArcFace 特征 + 余弦比对"，系统架构照搬虹软的"跟踪 ID + 廉价过滤 + 每人一个状态机 + 活体与特征并行 + 有界队列丢帧"。先搭对标台架量出差距，再按速度、活体、识别率的顺序逐项追平。

**三项的把握程度不同**：速度最有把握，差距主要来自工程浪费，换架构即可追平；识别率取决于模型选型和许可路线；活体最难，虹软的优势在大规模私有训练数据，追平的决定因素是我们在目标设备上采集的攻击样本。

---

## 1. 现状诊断：离线应用为什么问题多

### 1.1 设备端每一帧实际在做什么

| 环节 | 当前实现 | 代码位置 | 问题 |
|---|---|---|---|
| 相机帧 | CameraX YUV_420_888 → 拼成 NV21 → **JPEG 质量 100 压缩 → 再解码成 Bitmap** | [FaceRecognitionActivity.java:323-348](../../../android-sdk/app/src/main/java/com/bonuos/faceapp/FaceRecognitionActivity.java#L323-L348) | 每帧一次编解码，是整条链里最贵的无效开销；JPEG 有损；没有处理传感器旋转（代码里留有"可能需要处理旋转"的注释） |
| 节流 | `RECOGNITION_INTERVAL_MS = 1`，注释写的是 500ms | [FaceRecognitionActivity.java:49](../../../android-sdk/app/src/main/java/com/bonuos/faceapp/FaceRecognitionActivity.java#L49) | 实际没有节流，单线程串行跑完整流水线 |
| 活体 | Bitmap 再转一次 NV21 → ncnn 小检测器 → 两个 80×80 静默活体模型 | [LivenessDetector.java](../../../android-sdk/face-core/src/main/java/com/bonuos/facesdk/LivenessDetector.java) | 又一次像素格式转换；单帧硬阈值 0.7，结果逐帧抖动；只取面积最大的框 |
| 识别 | `libface_sdk_jni.so`（预编译，**仓库里没有源码**）：旋转分类 → FaceBoxes 640×640 → 5 点 → FSANet 姿态 → 对齐 → 102MB 识别模型 → 1024 维 | [FaceSDKWrapper.java](../../../android-sdk/face-core/src/main/java/com/facesdk/wrapper/FaceSDKWrapper.java)；`.so` 符号表可见 `FacePipeline::RunRotation/RunDetection/RunLandmark/RunPose/RunAlignment/RunRecognition` | 黑盒，无法修；每帧都跑 6 个模型，其中旋转分类模型只是为了补偿相机旋转没处理；和活体各跑一套检测器 |
| 1:N | Java 循环，**每个人一次 JNI 调用** `nativeCompare` | [FaceRecognizer.java:51-55](../../../android-sdk/face-core/src/main/java/com/bonuos/facesdk/FaceRecognizer.java#L51-L55) | JNI 往返与数组拷贝随人数线性增长；万人库时每帧上万次 JNI |
| 决策 | 识别结果直接刷 UI | [FaceRecognitionActivity.java:297-321](../../../android-sdk/app/src/main/java/com/bonuos/faceapp/FaceRecognitionActivity.java#L297-L321) | 没有跟踪、没有"每人只识别一次"、没有重试和冷却，同一个人站着不动会被反复识别，画面在"识别成功 / 未知人员 / 非活体"之间来回跳 |

虹软方案的核心是"把每帧计算变成每人计算一次"。当前实现恰好相反：**每帧都做最贵的事，而且做两遍检测、两遍格式转换。**

### 1.2 算法与数据层面的问题

- **模型来源与体积**：【推断】当前识别流水线与 SeetaFace6 开源版一致（256×256 对齐模板、248 裁剪、输出开方后 L2 归一化、1024 维）。识别模型 102MB，AAR 139MB，还打包了完整 OpenCV（calib3d、dnn、gapi 等）和 ONNX Runtime。
- **阈值四处不一致且从未校准**：

| 位置 | 值 |
|---|---|
| [FaceSearchService.java:56](../../../backend-java/src/main/java/com/bonuos/face/service/FaceSearchService.java#L56) 服务端搜索 | 0.6 |
| [config.yaml:5](../../../FaceFeatureExtractorAPI/config.yaml#L5) 算法服务 | 0.7 |
| [FaceRecognizer.java:12](../../../android-sdk/face-core/src/main/java/com/bonuos/facesdk/FaceRecognizer.java#L12) SDK | 0.65 |
| [SDK_INTEGRATION.md:111](../../../android-sdk/SDK_INTEGRATION.md#L111) 文档 | 0.80 |

- **特征没有真正的版本机制**：服务端固定字符串 `recognizer_v1_1024`，数据库 `feature_data` 是 JSON 文本，设备端 BLOB 只校验长度是 512 或 1024。换模型时无法安全共存，也无法发现错配。
- **注册服务的实现缺陷**（在旧流水线保留期间需要修）：旋转分类每次调用都新建 ONNX 会话（[face_feature_extractor.py:197](../../../FaceFeatureExtractorAPI/face_feature_extractor.py#L197)）；对齐函数每次调用都往 `./images/` 写调试 JPEG（[facealign.py:165-168](../../../FaceFeatureExtractorAPI/models/facealign.py#L165-L168)）；关键点 `shape_index_process` 是纯 Python 五层循环。
- **没有识别记录回传**：`sys_recognition_log` 表和实体存在，但没有任何设备接口写入。既无审计，也没有用于阈值校准的现场数据。
- **单帧活体**：静默活体只看一帧，没有"无法判断"与"不是活体"的区分。

### 1.3 已经做对、必须保留的部分

- 同步 v2（租约、墓碑、staging 原子发布、`FaceDatasetCoordinator` 读写锁），以及设备凭据、照片访问边界。新引擎只在"数据集发布"这一个点接入。
- 后端算法调用的有界并发、超时与就绪降级。
- `feat/TASK-2026-001` 分支里的校准概念（`CalibrationSummary`、`artifact_sha256`、`CONFIGURED_UNVALIDATED`），本方案的阈值下发沿用这一思路。

---

## 2. 目标：三项与虹软持平

### 2.1 "持平"的定义

所有对比都在**同一台目标设备、同一批输入帧、相同线程数**下进行。虹软的相似度是 0–1 映射分数，活体分数也是它自己的标度，因此**不比阈值，只在相同的错误率工作点上比另一项错误率**。

| 维度 | 主指标 | 持平判据 | 辅助指标 |
|---|---|---|---|
| 活体 | APCER（攻击被放过的比例）和 BPCER（真人被拒的比例），按 ISO/IEC 30107-3 口径 | 把我们的阈值调到与虹软默认阈值 0.5 相同的 BPCER，此时我们的 APCER ≤ 虹软；并分攻击类型（打印、屏幕翻拍、视频回放）逐类不劣于虹软。虹软 RGB 活体只输出"活体 / 非活体 / 未知"三态、不输出分数，所以它的错误率曲线靠 `setLivenessParam` 依次设多个阈值重放得到 | "无法判断"占比；真人从进入画面到通过活体的时延 |
| 速度 | 每阶段单次耗时：检测、特征、活体 | 每阶段 P50 和 P95 都 ≤ 虹软 | 端到端"进入画面到给出结果"的 P50 / P95；30 分钟连续运行后的帧率与温度；CPU 占用；内存峰值；包体 |
| 识别率 | 1:1 TAR@FAR=1e-5（身份核验）；1:N 首位命中率 | 在同一批负样本对上分别求出两家 FAR=1e-5 的阈值，此时我们的 TAR ≥ 虹软；1:N 在相同误识率下首位命中率 ≥ 虹软。FAR=1e-5 要求负样本对至少 30 万对才有意义，不够时报告只给 FAR=1e-4 并注明 | 现场"一次通过率"（人站定后 3 秒内核验成功的比例）；FAR=1e-4 时的 TAR |

统计要求：判"持平"时用 95% 置信区间而不是点估计。攻击样本零失误时，APCER 的 95% 上界约为 3/N，所以想证明 APCER ≤ 1% 至少要 300 次攻击尝试。样本量不够时报告写"未能证明持平"，不写"持平"。

### 2.2 对标台架

追平之前必须先量清楚差多少。现有"比虹软差"是体感，台架的作用是把它变成每一项的具体差距。

- **对标 App**（内部工具，不发布）：同一个 `FaceBackend` 接口下挂两个实现。`ArcSoftBackend` 只调用虹软公开的 `FaceEngine` API；`OurBackend` 先挂现有 SDK，阶段 1 后挂新引擎。
- **回放模式**：在目标设备上用 CameraX 录制原始 NV21 帧序列（不经压缩，保留翻拍的摩尔纹和屏幕纹理），两个后端逐帧读入完全相同的数据，输出逐帧 CSV：框、关键点、活体分数、特征、各阶段耗时。
- **实时模式**：两个后端交替接管相机，测端到端时延和长时间运行下的降频。
- **分析脚本**（`tools/face-eval/`）：从 CSV 生成 DET 曲线、各攻击类型的 APCER / BPCER、ROC、耗时直方图和对标报告。
- **虹软的使用边界**：本机已有 V5.0 SDK（`D:\arcface_study\sdk`）。只做黑盒测量，不参考其二进制分析结果，不在产品中使用其代码。开发者协议第八章第 2 项禁止未经书面许可创造衍生作品；第八章第 1 项要求使用该 SDK 的产品在知识产权申请中声明虹软，所以台架 App 不发布、产品不包含虹软任何文件，对标结论只用于内部决策。台架需要占用 1～2 个虹软免费激活名额。

### 2.3 测试数据

| 数据集 | 内容 | 起步规模（估算） | 用途 |
|---|---|---|---|
| 真人视频 | 目标设备摄像头录制；多时段、逆光、暗光、戴眼镜和口罩；不同距离 | 100 人 × 每人 5 段 | BPCER、识别正样本、时延 |
| 攻击视频 | 打印照（普通纸、相纸、裁出眼睛）、手机屏和平板屏翻拍、视频回放；多种屏幕型号和亮度 | 对应上述人员，每类每人 ≥ 3 次尝试 | APCER，逐类统计 |
| 注册照 | 与现场一致的注册流程采集 | 每人 1～2 张 | 识别率 |
| 1:N 干扰库 | 不出现在测试视频里的注册人员 | ≥ 1 万人特征 | 1:N 误识率 |

采集需书面知情同意，只用于评测和训练，数据集存放在受控存储，不进代码仓库。攻击样本同时是活体模型微调的训练数据，因此按人员划分训练集和测试集，同一人不能同时出现在两边。

### 2.4 工程目标（服务于三项持平）

1. 设备端连续视频识别：同一人只识别一次，画面不抖动。
2. 算法可替换：检测、特征、活体三个模型各自独立升级，特征带版本，错配即拒绝。
3. 注册与识别用同一套检测、对齐、特征模型，服务端与设备端的特征可互比，差异可量化。
4. 阈值由数据校准得出并随版本下发，不再散落在代码里。
5. 活体区分"不是活体"和"无法判断"，按场景可组合静默、交互、炫光。
6. 去掉无源码的 `.so`，引擎全部可构建、可测试。

### 2.5 非目标

- 不重写同步协议、管理后台或 Java 鉴权。
- 不从零训练识别模型（路线见 3.5）；活体模型需要在自有数据上微调，见 4.5。
- 不做跨进程引擎共享。

---

## 3. 总体架构

### 3.1 分层

```
┌──────────────────── 宿主 App ────────────────────┐
│ CameraX ImageAnalysis (YUV_420_888, rotation)    │
└──────────────┬───────────────────────────────────┘
               ▼ CameraFrame（平面指针 + 步长 + 旋转 + 镜像）
┌──────────── face-engine（新，纯算法，无网络无数据库）──────────┐
│ FaceSession：每个 trackId 一个状态机，调度下面四个引擎           │
│                                                                  │
│  FtEngine  VIDEO 模式：SCRFD 检测(低分辨率) + 跟踪器 → FaceInfo   │
│     │         {trackId, rect, 5 kps, score, yaw/pitch/roll, 越界} │
│     ▼                                                            │
│  过滤链（几乎零成本）：大小 → 稳定 → 区域 → 姿态 → 模糊/亮度       │
│     │ 只有通过的人脸继续                                          │
│     ├──► FlEngine 活体（有界队列，1 线程）──┐                     │
│     └──► FrEngine 对齐 112×112 + 特征（有界队列，1~2 线程）─┤       │
│                                               ▼                  │
│  Gallery：N×D 连续矩阵，NEON 点积 topN；双缓冲发布；版本校验      │
└──────────────┬───────────────────────────────────────────────────┘
               ▼ 识别事件（trackId, userId, score, liveness, reason）
┌──────────── face-core（现有：同步、SQLite、Repository）──────────┐
│ SyncStore.publish ──► Gallery.replaceAll(按 featureVersion 过滤)  │
│ 识别记录 outbox ──► POST /api/devices/recognitions（新）          │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 一个功能一个引擎

照搬虹软的组织方式，原因相同：规避同一句柄多线程并发，按需加载模型。

| 引擎 | 模式 | 输入 | 输出 | 线程 |
|---|---|---|---|---|
| `FtEngine` | VIDEO | 每帧（缩放后） | `List<FaceInfo>`，trackId 在人进出画面期间保持不变 | 相机分析线程 |
| `FrEngine` | IMAGE | 原分辨率帧 + 一张 `FaceInfo` | 特征向量 + `featureVersion` | 独立池，队列长度 = `maxDetectFaces` |
| `FlEngine` | IMAGE | 原分辨率帧 + 一张 `FaceInfo` | `ALIVE / NOT_ALIVE / UNKNOWN(reason)` + 分数 | 独立池，队列长度 = `maxDetectFaces` |
| `Gallery` | — | 特征矩阵 | topN 匹配 | 调用方线程，内部只读 |
| 注册用 `FaceEngine.analyzeImage()` | IMAGE | 单张图 | 检测 + 质量 + 特征 | 调用方线程 |

`FrEngine` 和 `FlEngine` 不再自己检测，直接用 `FtEngine` 的框和关键点，这正是虹软 `faceData` 的作用。

### 3.3 推理运行时

| 端 | 运行时 | 理由 |
|---|---|---|
| Android | **ncnn**（单一运行时） | 工程里已有静态库且活体已在用；SCRFD 和 ArcFace 有官方 ncnn 转换路径；支持 fp16、int8、Vulkan；可去掉 ONNX Runtime 和完整 OpenCV，包体显著下降 |
| 服务端 | ONNX Runtime（现有） | 注册、后台搜索、评测脚本都在 Python |

两个运行时跑同一个模型，必须有**跨运行时一致性测试**：同一批对齐后的 112×112 图，ncnn 与 ONNX Runtime 输出的余弦相似度 ≥ 0.995（阈值待实测后定）。这个测试是特征版本发布的门槛，见 6.4。

过渡期（阶段 1、2）设备端保留 ONNX Runtime 只为跑现有识别模型；阶段 3 换模型后移除。

**RK3568 走 NPU**：RK3568 只有 4 个 A55 小核，主频 2.0GHz，纯 CPU 跑特征模型会明显慢于手机；它带一个约 0.8 TOPS 的 NPU。虹软 SDK 只提供 CPU 版本，所以 NPU 是我们在这台设备上追平甚至超过虹软速度的主要手段。做法是推理层抽象成接口，手机走 ncnn，RK3568 走瑞芯微 RKNN 运行时（`librknnrt.so`，模型用 RKNN-Toolkit2 转换并做 int8 量化）。int8 量化后要重新跑 6.4 的一致性测试和 2.1 的精度对比。RKNN 运行时与工具链的许可需要核对，以及设备系统镜像里是否已带 NPU 驱动、驱动版本是多少，都要在阶段 0 确认。

### 3.4 模型选型

| 功能 | 候选 | 输入 | 体积 | 说明 |
|---|---|---|---|---|
| 检测 | SCRFD-500M-KPS（默认）；SCRFD-2.5G-KPS（高配或注册端） | 长边 320~640 | 0.57M 参数 | 自带 5 点关键点，省掉独立关键点模型；anchor 解码方式见笔记 2.2 |
| 特征 | MobileFaceNet / w600k_mbf（均衡档）；IResNet-50 / w600k_r50（精度档） | 112×112 RGB，(x−127.5)/127.5 | ≈5MB / ≈87MB(fp16) | **注册和识别必须同一模型**；因为跟踪保证每人只提一次，重模型在中端设备上也可接受，由 3.7 节在自有数据上与虹软对比后决定档位 |
| 对齐 | 5 点相似变换到 `arcface_dst` 模板 | — | — | 四自由度，不扭曲脸型 |
| 姿态 | 先用 5 点几何估计（眼线求 roll，鼻尖相对眼/嘴中线求 yaw，纵向比例求 pitch） | — | 0 | 只用于 ±30° 门控，不需要 FSANet；精度不够再加小模型 |
| 静默活体 | 沿用工程内 minivision 两模型（80×80，scale 2.7/4.0） | 框扩展裁剪 | 3.6MB | 已是 ncnn；增加时序投票和"无法判断"原因码；后续可换 InsightFace 活体插件或自研 |
| 旋转分类 | **删除** | — | — | 改用相机 `rotationDegrees`，对应虹软"场景确定时选单一检测角度" |

### 3.5 许可与商用路线（需要决策）

InsightFace 的代码是 MIT，但**预训练模型只允许非商业研究使用**（笔记第 0 节）。架构复刻不受影响，模型切换必须先定路线：

| 路线 | 做法 | 代价 |
|---|---|---|
| A. 购买商业授权 | 按 InsightFace `docs/commercial_evaluation.md` 联系获取 | 费用；最快 |
| B. 自行训练 | 用 `arcface_torch` 训练，需要许可允许商用的训练数据 | 常见公开训练集同样是研究许可，数据合规是真正的难点；周期长 |
| C. 评估其他许可明确允许商用的开源模型 | 用第 6 节的评测流程横向比较 | 精度不确定 |
| D. 保留现有模型（【推断】SeetaFace6，BSD-2 许可允许商用），只复刻架构 | 阶段 1、2 的默认状态 | 精度停在当前水平 |

推荐：阶段 1、2 按 D 推进，不阻塞。但现有模型大概率达不到识别率持平，所以 A/B/C 的决定要在阶段 0 对标报告出来后立即做，不能等到阶段 3。开发和评测期间可以直接使用 InsightFace 模型包做对比，上线前必须完成许可。

### 3.6 速度追平路线

现在每一帧串行做完下面所有事，没有任何一步因为"这个人已经识别过"而跳过：

| 步骤 | 现状 | 改法 |
|---|---|---|
| 格式转换 | YUV 拼 NV21，压 JPEG，解码 Bitmap | 原生层直接读 YUV 平面，转换与旋转一次完成 |
| 活体前再转格式 | Java 逐像素把 Bitmap 转回 NV21 | 删除，活体与识别共用一帧 |
| 检测 | 活体用一个检测器，识别再用一个 FaceBoxes 640×640 | 只保留一个 SCRFD-500M，长边 320～480 |
| 旋转分类 | 每帧跑一个 224 输入的 MobileNetV2 | 删除，用相机旋转角 |
| 关键点 | 两级网络，第二级前有逐元素的 shape index 处理 | SCRFD 直接输出 5 点 |
| 姿态 | 两个 FSANet | 5 点几何估计 |
| 特征 | 102MB 模型、248×248 输入，每帧都跑 | 每个 track 只跑 1～3 次；模型换成 112×112 输入 |
| 1:N | 每个人一次 JNI 调用 | 原生层一次扫完 |

优化手段的优先级：先减少调用次数（跟踪加状态机），再减少每次的计算量（换模型、降分辨率），最后才是底层加速（ncnn 的 fp16 和 int8 量化、绑定大核、预分配缓冲区、可选 Vulkan）。

速度的持平判据有两层：每阶段单次耗时不高于虹软，以及端到端"进入画面到给出结果"不慢于虹软 Demo 在同样过滤配置下的表现。后者受稳定帧数要求影响，两家用相同的大小和稳定过滤参数对比。

### 3.7 识别率追平路线

识别率由模型和流水线共同决定，两者分开测，避免把流水线问题误判成模型问题。

**模型**：在 2.3 节数据上横向比较以下候选，与虹软在 FAR=1e-4 上对比，选**达到持平的最小模型**：

| 候选 | 说明 |
|---|---|
| 现有 1024 维模型 | 基线 |
| MobileFaceNet（WebFace600K 训练） | 速度最好 |
| IResNet-50（WebFace600K 训练） | 均衡 |
| IResNet-100（Glint360K 训练） | 仅用于测精度上限，判断"换模型能不能追平" |

如果只有 IResNet-50 以上能追平，而目标设备上它的耗时超过虹软，就靠"每人只提 1～3 次特征"让端到端时延仍然持平，单次特征耗时这一项另行说明。

**流水线**：以下改动不换模型也能提高现场识别率，阶段 1 就做：

- 注册照严格门控：单人、正脸、清晰、亮度合格，不合格当场要求重拍。虹软要求注册用清晰无遮挡人脸并用精度更高的 IMAGE 模式，原因相同。
- 识别前质量门控：只对稳定、清晰、姿态合格的帧提特征，避免拿模糊帧去比。
- 多帧特征融合与 top-2 间隔：见 4.4 和 4.6，提高边缘样本的通过率，同时压住大库误识。
- 阈值按 FAR 校准：现在 0.6、0.65、0.7、0.8 四个值并存，任何一个都没有数据依据。

**可选实验**：识别成功且分数远高于阈值时，把现场对齐图的特征作为该人员的第二模板，缓解注册照与现场光照差异。需要防止错误识别污染底库，例如只在分数与 top-2 间隔都很大时追加，并由后台审核。是否启用看数据。

---

## 4. 设备端详细设计

### 4.1 相机输入

- CameraX `ImageAnalysis`，`STRATEGY_KEEP_ONLY_LATEST`，输出 YUV_420_888。分析线程把三个平面的指针、行步长、像素步长、`rotationDegrees`、是否前置一起传给原生层；**不再经过 JPEG 或 Bitmap**。
- 原生层用 ncnn 的 `yuv420sp2rgb` 加 `kanna_rotate` 完成格式转换和旋转，输出一张正立的 RGB 帧。前置镜像只在 UI 画框时处理，不翻转算法输入。
- 检测用缩放帧（长边默认 480，最低 320），对齐裁剪用原分辨率帧。关键点从缩放坐标映回原图。
- 分辨率策略和虹软的"最小人脸 80 像素"绑定：在原图 1280×720、缩放系数 0.5 时，80 像素的脸在检测输入上是 40 像素，SCRFD 步长 8 的层足够覆盖。

### 4.2 FtEngine：检测加跟踪

虹软 VIDEO 模式的两个产品特性要复刻：faceId 稳定、人脸框不跳。

- **检测**：每帧跑一次 SCRFD（低分辨率下成本很低，不做隔帧检测，避免引入运动预测的复杂性；基准测试证明必要时再加"每 N 帧检测、中间帧光流跟踪"）。
- **匹配**：贪心 IoU 匹配（≥ 0.3），IoU 相近时用 5 点中心距离打破平局。
- **丢失容忍**：连续 3 帧没匹配上才删除 track，避免检测偶发漏帧导致 ID 更换、状态机重置。
- **trackId**：进程内单调递增，加上持久化的基数（对应虹软 `trackedFaceCount` 存 SharedPreferences），重启后不重复，识别记录可按 trackId 追溯。
- **框平滑**：对 UI 输出做指数平滑，算法内部使用原始框。
- **越界标记**：框任一边超出图像 5% 以上标 `withinBoundary = false`，与虹软 `isWithinBoundary` 语义一致。

### 4.3 廉价过滤链

同一接口串成链，每个过滤器只处理前面已通过的人脸。顺序按成本排：

| 过滤器 | 规则 | 默认值 | 代价 |
|---|---|---|---|
| 大小 | 框宽和高都 ≥ `minFaceSize`（原图像素） | 80 | O(1) |
| 稳定 | 最近 5 帧相邻两帧中心位移都 ≤ `moveLimitPx` | 10 | O(5)；隐含"至少出现 5 帧"，前 4 帧一律不通过 |
| 区域 | 框完全落在识别区域内 | 关闭 | O(1) |
| 只保留最大脸 | 多人时只处理面积最大者 | 关闭 | O(n) |
| 姿态 | 5 点几何估计 |yaw| ≤ 30°、|pitch| ≤ 25° | 同现有配置 | O(1) |
| 清晰度 | 对齐后 112×112 灰度图的拉普拉斯方差 ≥ 阈值 | 待校准 | 一次 3×3 卷积 |
| 亮度 | 对齐图灰度均值在区间内 | 同现有 `brightness` 配置 | O(112²) |

清晰度和亮度在对齐之后做，因此放在 `FrEngine` 提特征之前，而不是检测之后。虹软用"稳定"代替清晰度检测，这里两者都保留，因为门禁场景人会边走边看。

### 4.4 FrEngine：对齐与特征

- 对齐：`SimilarityTransform` 从 5 点到 `arcface_dst`，`warpAffine` 到 112×112。自己实现四自由度最小二乘（现有 `facealign.py` 的 `transformation_maker` 就是同一个解，可直接移植到 C++）。
- 预处理：RGB，`(x − 127.5) / 127.5`。
- 输出：512 维，L2 归一化后存储和比对。
- **多帧特征融合**（对虹软方案的改进）：一个 track 最多收集 3 帧通过门控的特征，逐元素平均后重新归一化。首帧分数 ≥ `thresholdHigh` 直接判成功；落在 `[thresholdLow, thresholdHigh)` 之间则等下一帧融合后再搜；低于 `thresholdLow` 按失败重试。成本只是多一次搜索。
- `ExtractType.REGISTER / RECOGNIZE` 的区分保留为接口参数，当前两者走同一路径；将来接入口罩兼容模型时不改调用方。

### 4.5 FlEngine：活体

#### 4.5.1 现有活体为什么不如虹软

模型本身（minivision Silent-Face 的两个 MiniFASNet，80×80 输入）不是唯一原因，现有调用方式把能判断翻拍的信息先破坏掉了：

| 原因 | 现状 | 影响 |
|---|---|---|
| 输入被二次有损编码 | 相机帧先压成 JPEG，再解码成 Bitmap，再用整数近似公式转回 NV21 | 静默活体靠的是屏幕摩尔纹、反光、打印网点这类高频细节，两次有损转换正好把它们抹掉，真人和翻拍的分数都往中间靠 |
| 方向错误 | 传给模型的 `orientation` 固定为 0，但帧没有按传感器旋转 | 模型训练用的是正立人脸，竖屏设备上输入是横躺的人脸 |
| 人脸框粗糙 | 活体自带的检测器输入只有 192 像素宽，框再被强制改成正方形 | 两个模型按框的 2.7 倍和 4.0 倍外扩裁剪，框偏一点，裁到的上下文就变了 |
| 单帧硬判 | 每帧独立判断，阈值 0.7 写死、未校准 | 分数逐帧抖动，界面在"活体"和"非活体"之间跳 |
| 不区分无法判断 | 脸太小、太偏、出界时也输出分数 | 远处或侧脸的真人被判非活体，抬高 BPCER |
| 训练数据与现场不匹配 | 公开预训练权重，没见过我们的摄像头、补光和屏幕 | 活体检测对采集设备高度敏感，这是和虹软差距最大的一项 |

#### 4.5.2 逐级提升路线

每一级都在对标台架上量 APCER / BPCER，只保留确实有提升的改动。前两级不需要新模型。

| 级别 | 做法 | 预期作用 | 依赖 |
|---|---|---|---|
| L1 修输入 | 直接用相机原始 YUV 帧裁剪，按旋转角转正；人脸框改用 SCRFD 结果，并按两眼距离归一化成 minivision 训练时的方框尺度（框的定义变了，原阈值失效，必须重新校准） | 恢复高频翻拍线索；去掉方向错误 | 阶段 0 即可做 |
| L2 门控与时序 | 先判"能不能判"（下表原因码）；同一 track 最近 3 次评估 ≥ 2 次通过才算活体；只在稳定帧上评估 | 主要降低 BPCER，消除抖动 | 跟踪器（阶段 1） |
| L3 辅助线索（实验） | 在 4.0 倍外扩区域内找手机或平板的直线边框；对人脸区域做频域分析找摩尔纹；与模型分数用逻辑回归融合，权重在数据上拟合 | 针对屏幕翻拍这一最常见攻击 | 攻击数据集；没有收益就不上线 |
| L4 自有数据微调 | 用目标设备采集的真人与攻击数据微调 MiniFASNet（训练代码为 Apache-2.0；预训练权重的许可需核对），按人员划分训练集与测试集，再做 int8 量化 | 解决域不匹配，是追平虹软的主要手段 | 2.3 节数据集；1～2 张训练卡 |
| L5 主动挑战 | 交互动作（眨眼、张嘴、转头，随机顺序）或炫光（随机颜色序列），仅在核验场景开启 | 对视频回放类攻击有效；用户需配合 | 4.5.3 |
| L6 硬件（已取消：目标设备没有红外或深度摄像头） | 设备若有红外或深度摄像头，加红外活体 | 对打印和屏幕攻击效果最好；虹软 V5.0 不支持红外活体，这是唯一可能明显超过虹软的一项 | 目标设备硬件确认 |

公开的活体数据集（如 CelebA-Spoof、OULU-NPU）大多限研究用途，可用于实验对比，不能作为商用模型的训练数据，除非取得授权。

#### 4.5.3 主动挑战的实现要点

- 眨眼和张嘴：SCRFD 的 5 个点不够，需要加一个 106 点关键点小模型或专用的眼睛、嘴部开合分类器，用眼睛纵横比和嘴部纵横比判断。
- 转头：用 5 点几何姿态的 yaw 变化，要求先回正再转向指定方向。
- 每个动作有超时，动作序列随机抽取，期间若 trackId 变化则整个挑战作废，防止中途换人。
- 炫光：屏幕依次显示随机顺序的颜色，每种颜色取一帧，比较人脸区域在不同光照下的颜色响应；需要现场亮度条件下的数据验证后才能启用。

#### 4.5.4 运行时行为

- 静默活体两模型分数取平均，阈值随 profile 下发。
- **先判定能不能判**，再判是不是活体，返回值区分：

| 结果 | 条件 |
|---|---|
| `FACE_TOO_SMALL` | 框短边 < 80 |
| `FACE_ANGLE_TOO_LARGE` | 姿态门控未过 |
| `FACE_BEYOND_BOUNDARY` | `withinBoundary == false` |
| `FACE_NUM_MORE_THAN_ONE` | 严格模式下画面多于一张脸 |
| `NOT_ALIVE` / `ALIVE` | 分数与校准阈值比较 |

- **时序投票**：单帧模型噪声大，要求同一 track 最近 3 次评估中 ≥ 2 次 `ALIVE` 才算通过；`NOT_ALIVE` 同样投票后才记录，之后按 `livenessFailedRetryIntervalMs` 重试。
- 交互动作和炫光活体作为可插拔的 `Challenge` 实现，默认关闭，实现要点见 4.5.3。动作顺序和炫光颜色顺序必须随机。

### 4.6 Gallery：1:N

- 内存结构：`N × D` 行主序连续 float32 矩阵，另有 `ids[N]`，整体 L2 归一化。搜索在原生层用 NEON 点积一次扫完，返回 topN，**不再逐人 JNI**。
- 规模策略：1 万人 × 512 维 = 20MB，线性扫描估算 < 5ms；10 万人切 fp16（100MB）或 int8（50MB），估算几十毫秒，因为每人只搜一次所以可接受；再大才考虑 IVF 类索引。先不引入 FAISS。
- **双缓冲发布**：`SyncStore.publish` 完成后，在 `FaceDatasetCoordinator` 写锁内构建新矩阵，原子替换指针，旧矩阵在无读者后释放。空库同样发布。
- **版本校验**：加载时只接受 `featureVersion` 等于引擎 `FrEngine.featureVersion` 的记录，其余计数后跳过并上报；比对接口在版本不一致时返回 `MISMATCHED_FEATURE_VERSION` 而不是静默比错，对应虹软 73733。
- 第二名间隔：返回 top2，`best − second < topMargin` 时降级为"需要再确认"，走多帧融合，降低大库下的误识。

### 4.7 FaceSession：每个 trackId 一个状态机

`RecognizeInfo` 两个状态变量，与虹软 Demo 对应，但把"特征线程在 `waitLock` 上阻塞等活体"改成**事件合流**：特征和活体各自完成时写入 track 记录，谁后到谁触发决策，不占线程。

```
recognizeStatus:
  TO_RETRY ──提交特征任务──► SEARCHING
  SEARCHING ──提取失败 / 队列满(ERROR_BUSY)──► TO_RETRY            tries++
  SEARCHING ──tries > extractRetryCount──► FAILED ──等待 recognizeFailedRetryIntervalMs──► TO_RETRY
  SEARCHING ──特征就绪 且 liveness == ALIVE──► 搜索
      搜索 ≥ thresholdHigh（或融合后 ≥ thresholdHigh）──► SUCCEED
      搜索 ∈ [thresholdLow, thresholdHigh)──► 收集下一帧融合（最多 fusionFrames 帧）
      搜索 < thresholdLow 或融合用尽──► FAILED ──延迟──► TO_RETRY
  SUCCEED：本 trackId 不再提特征；记录识别事件；进入 succeedCooldownMs

liveness（recognizeStatus != SUCCEED 时处理）:
  UNKNOWN ──提交活体任务──► ANALYZING
  ANALYZING ──投票 ALIVE──► ALIVE（若特征已就绪则立即触发搜索）
  ANALYZING ──投票 NOT_ALIVE──► NOT_ALIVE ──等待 livenessFailedRetryIntervalMs──► UNKNOWN
  ANALYZING ──无法判断(原因码)──► UNKNOWN（记录原因供 UI 提示，如"请靠近"）
  ANALYZING ──接口失败 > livenessRetryCount──► 延迟后 UNKNOWN
  recognizeStatus 变为 FAILED 时重新做活体

清理：trackId 不在当前帧 → 删除记录；队列中尚未执行的任务按 trackId 作废。
```

补充两条虹软没有的规则：

- **按身份冷却**：同一 `userId` 在 `identityCooldownMs` 内再次 SUCCEED 不重复发事件，避免 track 偶然断裂导致一个人被记两次。
- **UI 事件去抖**：状态机只在状态变化时发事件，UI 不再逐帧刷新文字。

### 4.8 线程与队列

| 线程 | 职责 | 背压 |
|---|---|---|
| 相机分析线程（1） | 格式转换、检测、跟踪、过滤、状态机调度、投递任务 | CameraX 只保留最新帧 |
| FR 池（1，大核多时 2） | 对齐、提特征、搜索 | `ArrayBlockingQueue(maxDetectFaces)`，满了直接拒绝并按 `ERROR_BUSY` 走重试路径 |
| FL 池（1） | 活体 | 同上 |
| 回调 | 识别事件投递到主线程 | 无 |

任务携带帧的引用计数拷贝（只拷贝人脸区域及其外扩，不拷贝整帧），避免分析线程被阻塞。

### 4.9 配置项

对标虹软 `RecognizeConfiguration.Builder`，默认值作为起点，阈值类由校准结果覆盖：

| 配置 | 默认 | 说明 |
|---|---|---|
| `maxDetectFaces` | 3 | 也是两个队列的长度 |
| `detectLongSide` | 480 | 检测输入长边 |
| `minFaceSize` / `moveLimitPx` / `recognizeArea` / `keepMaxFace` | 80 / 10 / null / false | 过滤链 |
| `maxYaw` / `maxPitch` | 30 / 25 | 姿态门控 |
| `blurMin` / `brightnessRange` | 校准 | 对齐图质量门控 |
| `extractRetryCount` / `livenessRetryCount` | 3 / 3 | |
| `recognizeFailedRetryIntervalMs` / `livenessFailedRetryIntervalMs` | 1000 / 1000 | 虹软默认 0，这里给 1 s 避免空转 |
| `thresholdHigh` / `thresholdLow` / `topMargin` | 校准 | 随 `featureVersion` 下发 |
| `fusionFrames` | 3 | |
| `livenessThreshold` / `livenessVote` | 校准 / 2 of 3 | |
| `succeedCooldownMs` / `identityCooldownMs` | 3000 / 5000 | |
| `strictSingleFace` | false | 核验场景打开 |

### 4.10 SDK 公开接口（草图）

```kotlin
class FaceEngine private constructor(/* native handle */) : AutoCloseable {
    companion object { fun create(context: Context, config: EngineConfig): FaceEngine }
    val ft: FtEngine; val fr: FrEngine; val fl: FlEngine; val gallery: Gallery
    fun analyzeImage(bitmap: Bitmap, type: ExtractType): ImageAnalysis   // 注册 / 单图路径
}

data class FaceInfo(val trackId: Long, val rect: RectF, val kps: FloatArray /* 10 */,
                    val score: Float, val yaw: Float, val pitch: Float, val roll: Float,
                    val withinBoundary: Boolean)

interface FtEngine { fun process(frame: CameraFrame): List<FaceInfo> }
interface FrEngine { val featureVersion: String
                     fun extract(frame: CameraFrame, face: FaceInfo, type: ExtractType): FloatArray? }
interface FlEngine { fun detect(frame: CameraFrame, face: FaceInfo): LivenessResult }
interface Gallery  { val featureVersion: String
                     fun replaceAll(entries: List<GalleryEntry>): LoadReport
                     fun searchTopN(feature: FloatArray, n: Int): List<Match> }

class FaceSession(engine: FaceEngine, config: RecognizeConfiguration, listener: Listener) : AutoCloseable {
    fun onFrame(frame: CameraFrame)          // 相机分析线程调用
    interface Listener {
        fun onFaces(faces: List<FaceInfo>)                       // 画框
        fun onTrackState(trackId: Long, state: TrackState)       // 状态变化时才回调
        fun onRecognized(event: RecognitionEvent)                // SUCCEED
    }
}
```

现有 `FaceRecognizer.recognize(Bitmap)` 保留为门面，内部走 `analyzeImage` + `gallery.searchTopN`，让老接入方不改代码也能用新引擎。

### 4.11 模块与包体

```
android-sdk/
  face-engine/                     # 新：纯算法引擎
    src/main/cpp/engine/           # frame, image_ops, detector_scrfd, tracker, aligner,
                                   # embedder_ncnn, embedder_ort_seeta(过渡), liveness_silent,
                                   # quality, gallery
    src/main/cpp/jni/
    src/main/assets/models/manifest.json   # 每个模型的名称、版本、sha256、输入规格
    src/main/java/com/bonuos/faceengine/   # 上文接口 + filters/ + FaceSession
    src/test/                      # JVM 单测：跟踪器、过滤链、状态机（注入时钟）
  face-core/                       # 现有：同步、SQLite；FaceRecognizer 变为门面
  app/                             # 示例：CameraX → FaceSession
tools/face-eval/                   # 新：ROC 校准、跨平台一致性、性能汇总脚本
```

- 模型从 `AAssetManager` 直接加载，不再复制到 `filesDir`。
- 目标包体（估算）：引擎 so + ncnn + 模型（SCRFD-500M 2.5MB、MBF 约 5MB、活体 3.6MB）≤ 25MB；精度档加 r50 fp16 约 90MB。去掉完整 OpenCV 和 ONNX Runtime 后，当前 139MB 的 AAR 大部分可以省掉。

---

## 5. 服务端配套

### 5.1 注册流水线统一

- Python 服务新增 `POST /api/v2/extract_feature`：SCRFD（2.5G，长边 640）→ 5 点 → 112×112 对齐 → 同一特征模型，返回 `feature, feature_version, feature_dim, box, landmarks, quality{...}, reason_codes`。
- 注册门控比识别更严（对应虹软"注册用清晰无口罩人脸、用 IMAGE 模式"）：恰好一张脸（或最大脸面积占比 ≥ 阈值）、短边 ≥ 112、|yaw|、|pitch| ≤ 20°、清晰度和亮度通过。失败返回原因码，管理页提示重新上传。
- 旧 `/api/extract_feature` 在迁移期保留。若需继续运行，先修 1.2 节列出的三处实现缺陷。

### 5.2 特征版本化与迁移

- 新表 `sys_user_feature(user_id, feature_version, feature_dim, feature_blob BLOB, quality_json, extracted_at, PRIMARY KEY(user_id, feature_version))`。`sys_user.feature_data` 作为 v1 保留到旧设备全部退役。
- 后台重提取任务：遍历有照片的人员，对每个"配置启用的版本"提特征写入新表，有界并发复用现有算法槽，失败人员进入"需重新采集"列表。全量照片都在 `uploads/`，不需要用户重新上传。
- 一个人可以同时有多个版本的特征，因此混合设备群可以共存：每台设备只拿自己引擎版本的特征。

### 5.3 同步 v2 契约变更

- 设备在 bootstrap 和 changes 请求上声明 `featureVersion`；服务端按该版本渲染 `User.featureData` 并附 `featureVersion` 字段。没有该版本特征的人员照常下发但 `featureData = null`，设备入库后 Gallery 跳过并计数。
- 某个版本的特征集合变化（重提取完成、模型切换）视为该版本的 `scopeVersion` 变化，触发 `SNAPSHOT_REQUIRED` 重建。这与现有协议的语义一致，不需要新状态。
- 细节另写一份线协议增补，和 [同步 v2 契约](2026-09-24-sync-v2-wire-contract.md) 并列。

### 5.4 阈值校准与下发

- `tools/face-eval/calibrate.py`：输入标注的正负样本对（注册照 × 现场抓拍），输出 ROC，取目标误识率（通行 FAR = 1e-4，核验 1e-5）对应的阈值，写成 `recognition-profile.json`：`{feature_version, threshold_high, threshold_low, top_margin, liveness_threshold, dataset_sha256, calibrated_at}`。
- 后端提供 `GET /api/sync/v2/profile?featureVersion=`，设备同步时拉取并持久化；引擎没有拿到对应版本的 profile 时拒绝进入识别，而不是用代码默认值。
- 校准前的状态按 TASK-2026-001 分支的做法标 `CONFIGURED_UNVALIDATED`，UI 可以识别但记录里带标记。

### 5.5 识别记录回传（新接口）

- 设备 SQLite 新增 `recognition_outbox`，状态机每个 `SUCCEED` 和每个达到上限的 `FAILED` 写一行：`trackId, userId?, score, livenessScore, livenessResult, featureVersion, profileId, timestamp`，可选 112×112 对齐小图（约 5KB）。
- `POST /api/devices/recognitions`，设备 Bearer 凭据，批量 ≤ 100，幂等键 `deviceSn + trackId + timestamp`，写入 `sys_recognition_log`。
- 抓拍小图默认关闭，由后台按设备配置开启；这是获得现场校准数据的唯一途径，但涉及个人信息，需业务确认。

### 5.6 服务端 1:N 搜索（可选）

`FaceSearchService` 改读 `sys_user_feature` 的 BLOB，去掉逐行 JSON 解析；按分组缓存矩阵。阈值改读 profile。不在第一、二阶段范围内。

---

## 6. 评测与验收

### 6.1 指标

| 类别 | 指标 |
|---|---|
| 识别精度 | TAR@FAR=1e-4、1e-5；1:N 下的首位命中率与误识率 |
| 活体 | APCER（攻击被放过）、BPCER（真人被拒），按攻击类型分别统计；攻击样本至少覆盖打印照、手机屏翻拍、视频回放；与虹软的比较方法见 2.1 |
| 时延 | 每帧 FT 耗时；FR、FL 单次耗时；"人进入画面到 SUCCEED"端到端中位数和 P95 |
| 稳定性 | 8 小时连续运行内存曲线、温度、帧率衰减 |
| 一致性 | 服务端与设备端特征余弦相似度分布 |

### 6.2 数据

- 内部评测集按 2.3 节采集：现场人员注册照 + 多时段、多光照、多距离的抓拍，东亚人脸为主。笔记指出 InsightFace 官方模型在东亚子集成绩明显偏低，**任何模型上线前必须在自己的数据上重测**。
- 采集需要知情同意，评测集只存对齐小图和特征，不存原始帧。

### 6.3 性能预算（估算；验收以 2.1 节与虹软同机对比为准）

| 环节 | 中端设备目标 | 参考 |
|---|---|---|
| FT（检测 + 跟踪，长边 480） | ≤ 30 ms/帧 | 虹软文档检测 < 61 ms；SCRFD-0.5G 在桌面单线程 320×240 为 11.4 ms |
| FR（MBF / r50 fp16） | ≤ 80 ms / ≤ 400 ms | 虹软特征 < 30 ms（模型未知） |
| FL（两模型） | ≤ 40 ms | 虹软 RGB 活体 < 35 ms |
| 1:N（1 万人） | ≤ 5 ms | 虹软单次比对 < 0.01 ms |
| 端到端中位数 | ≤ 1 s | 过滤链要求至少 5 帧稳定，本身占 ≥ 170 ms |

### 6.4 跨平台一致性测试

固定 100 张对齐图作为黄金集：服务端 ONNX Runtime 与设备端 ncnn 输出逐一比较，余弦 ≥ 0.995（待定）；对齐矩阵逐元素误差 < 1e-3。该测试通过是发布新 `featureVersion` 的前置条件。

### 6.5 自动化测试

- JVM 单测：跟踪器（ID 保持、丢失容忍、重启不重复）、过滤链、状态机（注入时钟，覆盖重试、冷却、队列满、人离开清理、活体先到或后到两种顺序）。
- 原生测试：桌面构建 ncnn 引擎跑黄金集；anchor 解码对照 Python `scrfd.py`。
- 性能 APK：输出每阶段耗时直方图，作为阶段验收证据。

---

## 7. 分阶段实施

按"先量差距，再逐项追平"排序。速度、活体、识别率三条线在阶段 1 之后可以并行，每条线的退出条件都是第 2.1 节的持平判据。

### 阶段 0：对标台架、数据采集与止血（约 2 周）

- 对标 App 与分析脚本（2.2 节），先挂"现有 SDK"和"虹软"两个后端。
- 按 2.3 节采集第一批真人与攻击数据，同时作为后续活体微调的训练数据来源。
- 止血改动，只改现有 app 与 SDK，不动架构：去掉 JPEG 回路、按旋转角转正、恢复节流、活体与识别共用一次格式转换、识别成功后加冷却。这些同时就是活体 L1 的一半。
- 交付**基线对标报告**：现有 SDK、止血后 SDK、虹软三者在三项指标上的差距。

退出条件：报告给出每一项的具体差距和置信区间；据此确定 3.5 的许可路线和目标设备档位。

**进展（2026-10-12）**：

| 项 | 状态 |
|---|---|
| 对标 App `android-sdk/face-bench` | 已完成并编译通过：录制、`legacy_asis` / `legacy_fixed` / `arcsoft` 三个后端、回放、CSV 导出。未在真机运行 |
| 分析脚本 `tools/face-eval` | 已完成，合成数据上测试通过 |
| 示例 App 止血 | 已完成并编译通过：去掉 JPEG 回路、按旋转角转正、活体三帧投票、识别成功后保持 3 秒。未在真机运行 |
| 实时模式（端到端时延） | 未做 |
| 数据采集、基线报告 | 待在两类目标设备上执行 |

录制做了两处取舍：每秒最多存 10 帧、默认每段 2 秒，一段约 28MB，避免 RK3568 存储放不下。虹软 V5.0 要求 Android 10 及以上，RK3568 的系统版本需要先确认。

**进展（2026-10-13）：不换模型的架构与工程优化**。约束：所有现有模型（FaceBoxes、两级关键点、FSANet、1024 维识别、minivision 检测与活体）不变，存量特征继续可用。

| 项 | 内容 | 验证 |
|---|---|---|
| 阶段 1 的 Java 层提前落地 | `face-core` 新增 `com.bonuos.facesdk.engine`：IoU 跟踪器（丢失容忍 3 帧）、过滤链（大小、越界、多人、只保留最大脸、稳定）、对齐前亮度与相对清晰度门控、每个 track 一个状态机（活体投票、特征与活体事件合流、多帧特征融合、重试退避、按身份冷却、原因码）、`Gallery`（连续归一化矩阵一次扫描求 top-2，替代逐人 JNI） | 39 项 JVM 单测通过；未在真机运行 |
| 原生层一次转换 | 新增 `UprightFrame`：NV21 转 BGR 与旋转在原生层各做一次，检测和两个活体模型共用。原来每帧 Java 整帧旋转一次、原生再转换两次 | 与旧路径逐像素一致（三个旋转角）；导出符号已在两个 APK 里核对 |
| 识别输入改为人脸裁剪 | 识别模型收到跟踪框 2.2 倍的裁剪（`featureCropScale`，0 = 整帧），只转换裁剪区域；保证特征来自被跟踪的人 | 对识别率的影响待台架 `engine_crop` 与 `legacy_fixed` 对比 |
| `.so` 比对分值核对 | 加载人员库时用真实特征对比较 Java 余弦与 `nativeCompare`，差值 > 1e-3 时改用原生比对，阈值口径不变 | 需在真机上看日志确认走的是哪条路径 |
| Python 注册服务 | 旋转分类会话只建一次（原占单次请求约 46% 耗时）；anchor 预生成、框解码与 shape-index 向量化；质量门控提到识别模型之前；删除对齐时把人脸写到 `./images/` 的调试代码 | 40 张图的框、关键点、质量判定、特征与改动前逐位一致；平均 48→38 ms；等价性单测已加 |
| 服务端 1:N | 流式解析特征 JSON 到 `float[]`（不装箱）、探针只归一化一次；阈值改为 `face.search.threshold`（默认仍 0.6） | 后端 174 项测试通过（34 项 MySQL 集成测试本机跳过） |

仍未做：实时模式（端到端时延）、SCRFD 与 5 点几何姿态（属换模型）、阈值校准与 profile 下发、识别记录回传。

**进展（2026-10-13，阶段 A：自研引擎替换无源码的 `libface_sdk_jni.so`）**。决定：先 A 后 B，B 做成可切换的第二个特征版本。测试机 OPPO PEPM00（天玑 1200，1×A78 3.0G + 3×A78 2.6G + 4×A55，Android 13）。

| 项 | 内容 | 验证 |
|---|---|---|
| `libfaceengine.so` | `face-core/src/main/cpp/engine`：ONNX Runtime 1.23.2（与原 `.so` 同一构建），逐步移植 Python 注册服务的 FaceBoxes、两级关键点、Seeta 256 对齐、248 识别，连 NumPy 2 的 float32 运算顺序都对齐（编译用 `-ffp-contract=off`）。模型从 APK 直接映射，不再复制到 `filesDir` | 手机上 200 张图：检测结果与 Python 完全一致；93% 的人脸关键点逐位相同，中位余弦 1.000000 |
| 去掉的步骤 | 旋转分类（用相机旋转角）；视频流里不跑 FaceBoxes（用跟踪框，两种框的几何关系中位数为恒等）；FSANet 换成 5 点几何姿态（`Pose5`，按 30°/25° 门限判断正脸，与 FSANet 结论一致率 97.3%） | 见下表 |
| 关键点 TTA | 关键点不再截断到整数，并对 5 个微抖动框取平均。截断会把平台间极小的浮点差放大成 2～8 像素的跳动 | LFW 全配对 TAR@1e-5：服务端不变 × 设备端 TTA 为 89.04%，现状为 88.32% |
| int8 识别模型 | 同一模型静态量化（只量化卷积，首层保持 fp32，Percentile 校准），102MB → 32MB | 设备端 int8+TTA × 服务端 fp32 注册：TAR@1e-5 为 88.67%，不低于现状 |
| 单次提特征耗时（手机实测，4 线程中位数） | 原流水线 6 个模型（模拟原 `.so`）109 ms；新引擎 fp32+TTA 55 ms（−50%）；**int8+TTA 34 ms（−69%）** | `face_engine_cli bench` |
| App 默认 | `FaceRecognizer.Backend.NATIVE_INT8` + `NativeFeatureEngine`（TTA，姿态门控）；旧 `.so` 路径保留为 `LEGACY_SDK` | 台架新增 `engine_native`、`engine_native_int8` 两个后端 |

LFW 上的阈值（只作参考，必须用自有数据重新校准）：FAR=1e-3 / 1e-4 / 1e-5 分别对应约 0.57 / 0.64 / 0.70。设备默认 0.65 大约相当于 FAR 1e-4，达不到核验场景要求的 1e-5。

结论：不换模型，速度能追到原来的约三分之一；准确率只能提升约 0.5～0.7 个点（LFW 上 TAR@1e-5 约 88～89%）。要大幅提准，靠阶段 B 换模型。评测脚本在 `tools/engine-eval/`。新状态机的阈值默认值沿用旧值，`thresholdLow`、`topMargin`、`minSharpness` 待台架数据校准。

### 阶段 1：速度追平（约 3～4 周）

交付 `face-engine` 模块：SCRFD 检测、跟踪器、过滤链、Gallery、FaceSession 状态机、活体 L1 和 L2。`FrEngine` 第一版是**过渡适配器**，仍用现有 1024 维识别模型（由我们自己的 C++ 通过 ONNX Runtime 调用），保证特征 v1 兼容，**服务端和同步协议零改动**。黑盒 `.so` 在这一阶段被替换。

退出条件：检测、活体两阶段单次耗时 P50 / P95 不高于虹软；端到端时延不慢于虹软；30 分钟连续运行无明显降频；每人识别一次、画面不抖；状态机单测通过；新引擎特征与现有 Python 服务余弦 ≥ 0.99。特征单次耗时在换模型前预计仍慢于虹软，留到阶段 3 关闭。

### 阶段 2：活体追平（与阶段 1 后半段及阶段 3 并行，约 4～6 周）

- L3 辅助线索实验，保留有收益的部分。
- L4：用阶段 0 起持续采集的数据微调 MiniFASNet，量化后接入 `FlEngine`；阈值写入 profile。
- 若场景是身份核验，加 L5 主动挑战；若设备有红外摄像头，评估 L6。

退出条件：在留出测试集上满足 2.1 的活体持平判据，逐攻击类型不劣于虹软；样本量足以给出置信区间。数据不足时，本阶段的结论只能写"未证明持平"。

### 阶段 3：识别率追平（约 4 周，入口条件：3.5 许可路线已定）

设备端 `FrEngine` 换成 3.7 选定的模型（ncnn），服务端上线 v2 注册接口和 `sys_user_feature`，后台重提取，同步协议加 `featureVersion`，校准工具与 profile 下发上线。在预发环境用全量人员库演练迁移和回滚。

退出条件：满足 2.1 的识别率持平判据；特征单次耗时不高于虹软，或端到端时延仍持平并在报告中说明；6.4 一致性测试通过；混合版本设备群同步正确；回滚演练有记录。

### 阶段 4：现场验收（约 2 周）

识别记录回传接口上线；试点设备与虹软对照机并排运行一周，比较现场一次通过率、误识和活体拦截情况；验收报告注明未验证项。

---

## 8. 风险与需要确认的决策

| 风险 | 应对 |
|---|---|
| 活体追平依赖攻击数据，数据不够就无法证明持平 | 阶段 0 就开始采集并持续积累；覆盖多种屏幕和打印介质；按人员划分训练与测试 |
| 活体模型对采集设备敏感，换设备后精度下降 | 每种目标设备型号单独采集验证数据；profile 按设备型号下发阈值 |
| 只有大模型才能追平识别率，单次耗时超过虹软 | 跟踪保证每人只提 1～3 次特征，端到端时延仍可持平；量化后重测精度 |
| 模型商用许可 | 阶段 0 结束时定路线（3.5）；阶段 1 不依赖 InsightFace 模型 |
| 东亚人脸精度低于官方数字 | 2.3 节自有数据先测再选型 |
| 低端设备热降频 | 过滤链降低进入重模型的次数；检测分辨率可降到 320；台架覆盖最低档设备 |
| 虹软协议约束 | 只做黑盒测量，台架不发布，产品不含虹软文件 |
| 全量重提取耗时 | 后台有界任务，可中断续跑；旧版本并存不影响线上 |
| ncnn 转换精度偏差 | 6.4 一致性测试作为发布门槛；偏差过大则该模型改走 ONNX Runtime Mobile |
| 采集数据和识别记录涉及个人信息 | 书面知情同意；受控存储；抓拍小图默认关闭，保留期可配 |

**决策状态**

| 决策 | 状态 |
|---|---|
| 目标设备 | 已定：安卓手机、RK3568。还需要具体手机型号和 RK3568 设备的系统版本、摄像头接法 |
| 红外或深度摄像头 | 已定：没有，L6 取消 |
| 场景定位 | 已定：身份核验，FAR=1e-5，开启主动挑战 |
| 数据采集 | 已定：可以组织 |
| 虹软 SDK | 已定：已付费，可调用 |
| 比对底图与比对方式 | 按假设推进：平台登记照、1:1。若是身份证芯片照需告知 |
| 模型商用路线 | 待定：阶段 0 报告后决定，见 3.5 |
| 识别记录回传 | 待定 |

---

## 附录 A：与两篇笔记的逐条对照

| 笔记要点 | 本方案 |
|---|---|
| 虹软 11.1 跟踪 ID 把每帧计算变成每人一次 | 4.2 跟踪器 + 4.7 `SUCCEED` 后不再提特征 |
| 虹软 11.2 按成本分层过滤 | 4.3 过滤链，顺序按成本 |
| 虹软 11.3 区分"不是活体"和"无法判断" | 4.5 原因码 |
| 虹软 11.4 活体先于比对但并行 | 4.7 事件合流，不阻塞线程（改进） |
| 虹软 11.5 有界队列丢帧、有限次重试 | 4.8 队列长度 = `maxDetectFaces`；4.9 重试与间隔 |
| 虹软 11.6 一个功能一个引擎 | 3.2 |
| 虹软 11.7 单一检测角度、只保留最大脸、识别区域 | 4.1 用相机旋转替代旋转分类模型；4.3 可选过滤器 |
| 虹软 11.8 随机挑战 | 4.5 `Challenge` 顺序随机 |
| 虹软 11.9 特征带版本 | 4.6 版本校验；5.2 多版本表；5.3 协议字段 |
| InsightFace 2 SCRFD | 3.4 检测选型；anchor 解码对照 `scrfd.py` |
| InsightFace 3 五点相似变换 | 4.4 |
| InsightFace 5 阈值无通用值 | 5.4 校准工具与 profile 下发 |
| InsightFace 6 东亚子集精度 | 6.2 自有评测集 |
| InsightFace 7 对照表中"要自己补的部分"（跟踪、旋转、质量门、1:N、版本、交互活体） | 4.2、4.1、4.3、4.6、5.2、4.5.3 |
| InsightFace 0 许可 | 3.5 决策项 |
| 虹软 Demo 用 `wait/notify` 等活体 | 不采用，改事件合流 |
| 虹软 Demo 固定动作顺序 | 不采用，随机 |
| 虹软设备授权激活 | 不需要 |
