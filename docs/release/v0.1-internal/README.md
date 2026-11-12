# 离线人脸引擎 v0.1-internal（内测定版）

**定版日期**：2026-10-16  
**定版代码**：分支 `yuanzq/feat/offline-face-engine`，标签 `v0.1-internal`（指向加入本目录文档的提交；代码部分截至 `faad773`）  
**用途**：在适用许可允许的范围内开展内部非商业学习研究和与虹软 ArcFace Pro V5.0 对标。**未取得商用授权，不用于正式业务部署或商用发布**，原因见下文“授权限制”。

> 2026-10-16 冻结后更新：补充事实勘误及 [05-分析线程优化实验.md](05-分析线程优化实验.md)。研究方案与冻结版本的已实现能力分开记录；不移动原标签，不修改历史运行证据。

## 本版包含什么

| 模块 | 内容 |
|---|---|
| 识别模型 | MobileFaceNet → IResNet-50 int8 两级级联（InsightFace w600k 权重） |
| 检测 | SCRFD-500M + 局部区域重检测跟踪 |
| 活体 | 静默 RGB 活体（MiniFASNet 两模型，ncnn），3 帧里 2 帧判为真人才通过 |
| 运行时 | 自研 `libfaceengine.so`（C++，ONNX Runtime 1.23.2 CPU，OpenCV，ncnn） |
| 安全护栏 | 任务代际校验、统一成功门禁、图库发布代、有界收尾、推理与释放互斥 |
| 对标工具 | face-bench 台架（含虹软会话后端）、`tools/engine-eval/session_compare.py` 审计报告 |

两项性能优化已实现但**默认关闭**，等复测通过后再决定是否打开：
- 级联两级共用一次裁剪（`SessionConfig.shareFeatureCapture`，构建参数 `-Pface.shareCapture=true`）；
- 打包前跳过空闲帧（构建参数 `-Pface.earlyAdmission=true`）。

## 文档索引

| 文件 | 内容 |
|---|---|
| [01-技术路线与架构.md](01-技术路线与架构.md) | 技术路线的演进、整体架构、每帧处理流程、线程模型、关键参数 |
| [02-与虹软差距及改进路线.md](02-与虹软差距及改进路线.md) | 实测对比、逐项差距、原因分析、改进方案和优先级 |
| [03-目标设备与测试计划.md](03-目标设备与测试计划.md) | 安卓手机实测情况、RK3568 测试计划（设备未到）、测试步骤 |
| [04-验证记录与已知问题.md](04-验证记录与已知问题.md) | 自动化测试、真机测试记录、证据存放位置、已知问题清单 |
| [05-分析线程优化实验.md](05-分析线程优化实验.md) | 冻结后的研究方案：授权边界、分阶段测量、等价优化、调度/光流实验、验收与回退 |
| [06-第一性原理复盘报告.md](06-第一性原理复盘报告.md) | 冻结后复盘：对标目标重定义、算力/时延/误识/活体账、Android 接口问题、研究项排序与风险 |

相关的早期文档：[离线引擎设计文档](../../superpowers/specs/2026-10-12-offline-face-engine-design.md)、[37 服务器测试设备记录](../../reports/2026-10-13-server37-test-device.md)。

## 构建

环境：JDK 19、Gradle 8.2、Android SDK 34、NDK 25.1，ABI 只打 `arm64-v8a`。

```bash
cd android-sdk
gradle :face-core:testDebugUnitTest :face-bench:testDebugUnitTest :app:testDebugUnitTest
gradle :app:assembleDebug :face-bench:assembleDebug
```

- InsightFace 权重不进仓库，构建时用 `local.properties` 的 `insightface.assets` 指向本地目录。
- 识别引擎由 `face.engine` 选择，默认是 `insightface_cascade`。
- **APK 默认不嵌入同步设备凭据。** 只有给某台测试机单独开通时，才加 `-Pface.includeLocalSync=true`，并且这样打出的包不得外发。

## 授权限制（定版的硬约束）

- InsightFace 的代码是 MIT 许可，但本地查阅记录中的**预训练权重仅允许非商业研究使用**；项目没有已取得商用权重授权的证据。“内部使用”不自动覆盖商业研发、门禁或考勤业务部署。
- 商用前需取得适用的商业授权，或使用自有合规数据训练、权重许可允许商用的替代方案；同时核查训练数据、活体权重和第三方组件。换模型后需重新验证精度、阈值和性能。
- 本轮先做许可范围内的非商业学习研究，不把采购授权作为性能实验的前置任务；授权细节及资料核验状态见 05。
- 虹软 SDK 只通过公开 API 做黑盒对照，不复制、不移植其实现，SDK 文件不进仓库；设备激活不代替合同及使用范围核查。
