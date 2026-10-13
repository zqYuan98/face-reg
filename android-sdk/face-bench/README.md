# 人脸对标台架（face-bench）

内部测量工具，不对外发布。它在同一台设备上，把**同一批原始相机帧**依次交给各后端，测活体、识别率和速度，结果交给 [`tools/face-eval`](../../tools/face-eval/README.md) 生成对标报告。方案背景见[设计文档第 2 节](../../docs/superpowers/specs/2026-10-12-offline-face-engine-design.md)。

| 后端 | 含义 |
|---|---|
| `legacy_asis` | 现有 SDK，按旧版 App 的方式喂数据：传感器方向、JPEG 往返、Bitmap 再转 NV21 |
| `legacy_fixed` | 现有 SDK，阶段 0 修复后的输入：原始 NV21 转正，无 JPEG |
| `engine_crop` | 同样的模型，按新 `FaceSession` 的方式喂数据：检测和活体共用一次原生格式转换；识别模型收到人脸周围 2.2 倍裁剪图而不是整帧（无人脸时退回整帧）。与 `legacy_fixed` 对比可看出这两项各自对耗时和识别率的影响 |
| `engine_native` | 自研引擎 `libfaceengine.so`（同一套模型，fp32）：跟踪框 → 5 次抖动框平均的关键点 → 对齐 → 识别，不跑旋转分类、检测和 FSANet。注册照按服务端步骤提取 |
| `engine_native_int8` | 同上，识别模型换成 int8 量化版（`face_recognizer_int8.onnx`） |
| `arcsoft` | 虹软 ArcFace V5.0，只通过公开 API 调用，组织方式与其 Demo 一致 |

## 1. 构建

虹软 SDK 不进仓库。在 `android-sdk/local.properties`（已被 git 忽略）里写上本机虹软 `libs` 目录：

```properties
sdk.dir=C:/Users/<you>/AppData/Local/Android/Sdk
arcsoft.sdk.libs=D:/rea/ArcSoft_ArcFaceS_Android_V5.0/libs
```

```powershell
cd android-sdk
gradle :face-bench:assembleDebug
adb install -r face-bench\build\outputs\apk\debug\face-bench-debug.apk
```

不配置这个路径也能构建，只是没有 `arcsoft` 后端。只支持 arm64-v8a。**虹软 V5.0 要求 Android 10 及以上**，RK3568 设备的系统版本低于 10 时无法对比虹软。

## 2. 首次激活虹软

设备联网，打开 App，在"虹软激活"里填 APP_ID 和 SDK_KEY，点"激活虹软"。返回码 0 或 90114 都表示成功，之后可以离线。凭据只保存在 App 私有存储里，不写入日志和结果文件。每台测试设备占用一个激活名额。

## 3. 准备注册照

用与平台注册流程相同的照片，文件名就是人员编号，放到设备上：

```powershell
adb shell mkdir -p /sdcard/Android/data/com.bonuos.facebench/files/enroll
adb push p001.jpg /sdcard/Android/data/com.bonuos.facebench/files/enroll/
```

文件名只能用字母、数字、点、横线、下划线。照片的 EXIF 方向会被自动转正，各后端用同一张转正后的图。

## 4. 录制样本

"录制样本"界面每次录一次尝试（一个片段），默认 2 秒、最多每秒 10 帧，1280×720 约 28MB。人员编号必须和注册照文件名一致，攻击片段的人员编号填"被冒充的人"。

**每台目标设备分别录**，摄像头不同，结果不能混用。建议的采集量：

| 类别 | 每人 | 说明 |
|---|---|---|
| 真人 | 5 段 | 室内正常、逆光、暗光、戴眼镜、不同距离各一段 |
| 打印照 | 3 段以上 | 普通纸彩打、相纸、挖掉眼睛的打印照；平放、弯曲 |
| 屏幕翻拍 | 3 段以上 | 至少 3 种不同品牌的手机和平板，最高亮度和中等亮度 |
| 视频回放 | 3 段以上 | 在屏幕上播放此人的视频 |

每类攻击至少 100 个片段才会出判定，要证明攻击放过率不超过 1% 需要约 300 个。录制前须取得书面知情同意，数据只用于评测和活体模型训练。

## 5. 运行与导出

点"运行对标"。后端逐个运行，互不抢占资源。片段多时可以分批：把暂不跑的片段移出 `clips/`。

```powershell
adb pull /sdcard/Android/data/com.bonuos.facebench/files/runs/<runId> .\runs\
cd ..\tools\face-eval
python -m faceeval.report ..\..\android-sdk\runs\<runId> --ours legacy_fixed --ref arcsoft
```

报告写在运行目录下的 `report.md` 和 `report.json`。

## 6. 输出文件

| 文件 | 内容 |
|---|---|
| `run.json` | 设备信息、配置、各后端版本说明、失败原因 |
| `frames.csv` | 每个后端每一帧：人脸数、框、各阶段耗时、活体分数和状态、是否提到特征 |
| `liveness_sweep.csv` | 虹软每帧在各个活体阈值下的状态（它不输出分数） |
| `scores.csv` | 每个提到的特征与每张注册照的相似度 |
| `enroll.csv` | 每张注册照在每个后端是否提取成功 |

活体状态码沿用虹软定义：1 活体，0 非活体，-1 未知，-2 多人脸，-3 脸太小，-4 角度过大，-5 超出边界，-100 未运行，-101 出错。

## 7. 已知局限

- 回放只测各阶段耗时，不测"进入画面到给出结果"的端到端时延，这需要后续的实时模式。
- 回放时 CPU 温度会随运行时间上升，长时间运行后的降频要另外看。
- `legacy_asis` 的框在传感器方向坐标下，与其他后端的框不可直接比较。
