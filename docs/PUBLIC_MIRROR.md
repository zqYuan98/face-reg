# 公开仓库说明

本仓库是人脸识别平台源码的公开镜像，包含算法服务（`FaceFeatureExtractorAPI`）、Java 后端（`backend-java`）、Android SDK（`android-sdk`）和评测工具（`tools`）。与内部仓库相比，**全部提交历史**中删去或替换了以下内容，因此提交号与内部仓库不同。

## 未包含的内容

| 内容 | 原因 | 获取方式 |
|---|---|---|
| 模型权重：`FaceFeatureExtractorAPI/checkpoints/*.onnx`、`android-sdk/face-core/src/main/assets/` 下的 `.onnx`，以及 `detection/`、`live/` 下的 `.bin` / `.param` | 来源与再分发许可未核实 | 自行准备同名文件放到原路径。代码按文件名加载；v1 特征版本 `recognizer_v1_1024` 依赖这些模型 |
| InsightFace 权重（`checkpoints/insightface/`，v2 特征版本 `arcface_r50_512`） | 官方权重仅限非商业研究用途，内部仓库同样不收录 | 从 InsightFace 官方发布获取 `buffalo_l`、`buffalo_s`，见 [FEATURE_V2.md](../FaceFeatureExtractorAPI/FEATURE_V2.md) |
| `android-sdk/face-core/src/main/jniLibs/arm64-v8a/libface_sdk_jni.so`、`android-sdk/app/libs/face-sdk-release.aar` | 第三方预编译库，无源码，许可不明；当前构建已不使用 | — |
| `android-sdk/face-core/.cxx/` | 本机 CMake 构建缓存 | 构建时自动生成 |
| 服务器部署与运维记录（`docs/reports/` 下的部署、运维手册、证据文件，`docs/DEPLOYMENT_HANDOFF_*`） | 内部环境信息 | — |
| 第三方商业 SDK 的分析材料（`face-notes/` 下相关报告） | 涉及厂商授权材料 | — |

其余开源组件（ONNX Runtime、OpenCV、ncnn、libc++）的预编译库保留在 `jniLibs/`，分别遵循其各自的开源许可。

## 文本替换

历史中的内网地址替换为 `<server-37>`、`<git-server>`、`<lan-host>`，服务器用户目录替换为 `/home/<user>`，曾出现过的明文口令替换为 `***REMOVED***`。代码中的私有网段配置（如 `192.168.0.0/16`）未改动。

## 许可提醒

- 本仓库不授予任何模型权重的使用权；使用 InsightFace 权重请遵守其许可。
- 人脸数据属于个人敏感信息，部署和测试须取得授权，遵守所在地法律法规。

入口文档：[CODE_MAP.md](CODE_MAP.md)（代码地图）、[face-http-api.md](face-http-api.md)（HTTP 接口）、[API_DOCUMENTATION.md](../API_DOCUMENTATION.md)。
