> 2026-09-24 源码更新：同步 v2、配置 API、SQLite v3 和验证边界见 [SYNC_V2.md](SYNC_V2.md)。本文件下述历史 AAR/固定地址描述仅适用于旧版本；仓库旧 AAR 尚未重新发布。

# Android SDK 集成文档

## 概述

Face SDK 是一个离线人脸识别 SDK，支持人脸检测、特征提取、活体检测和本地识别。

## 集成步骤

### 1. 添加 AAR 文件

将 `face-core-release.aar` 复制到您的项目 `app/libs` 目录下。

### 2. 配置 Gradle

在 `app/build.gradle.kts` 中添加：

```kotlin
android {
    defaultConfig {
        ndk {
            abiFilters.add("arm64-v8a")
        }
    }
}

dependencies {
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar"))))
    implementation("com.google.code.gson:gson:2.10.1")
}
```

### 3. 初始化 SDK

在 Application 或 Activity 中初始化：

```java
import com.bonuos.facesdk.FaceRecognizer;
import com.bonuos.facesdk.SyncManager;

public class MyApplication extends Application {
    private FaceRecognizer faceRecognizer;
    private SyncManager syncManager;

    @Override
    public void onCreate() {
        super.onCreate();
        
        // 初始化识别器
        faceRecognizer = new FaceRecognizer(this);
        
        // 初始化同步管理器
        syncManager = new SyncManager(this);
        
        // 同步数据
        syncManager.syncData();
    }
}
```

### 4. 使用人脸识别

```java
import android.graphics.Bitmap;
import com.bonuos.facesdk.FaceRecognizer;

// 识别人脸
Bitmap bitmap = ...; // 从相机或图片获取
FaceRecognizer.RecognitionResult result = faceRecognizer.recognize(bitmap);

if (result.success) {
    String userName = result.user.name;
    float similarity = result.similarity;
    Log.i("FaceSDK", "识别成功: " + userName + ", 相似度: " + similarity);
} else {
    Log.w("FaceSDK", "识别失败: " + result.message);
}
```

### 5. 数据同步

```java
import com.bonuos.facesdk.SyncManager;

SyncManager syncManager = new SyncManager(context);

// 配置服务器地址（在 SyncManager.java 中修改 SYNC_URL）
// 触发同步
syncManager.syncData();
```

## API 参考

### FaceRecognizer

#### 构造方法
```java
FaceRecognizer(Context context)
```

#### 主要方法

**recognize(Bitmap bitmap)**
- 参数: `Bitmap` - 待识别的人脸图片
- 返回: `RecognitionResult` - 识别结果
- 说明: 执行人脸识别，返回匹配的用户信息

**setThreshold(float threshold)**
- 参数: `float` - 识别阈值 (0.0-1.0)
- 说明: 设置识别阈值，默认 0.65（未经校准，应按 FAR 校准后设置）

**release()**
- 说明: 释放资源

**gallery()**
- 返回: `Gallery<FaceUser>` - 当前人员库的不可变快照（归一化特征矩阵，一次扫描求 top-2）
- 说明: 同步发布后自动重建。特征缺失、维度不符、全零或非有限值的人员会被跳过并计数，不参与比对

### FaceSession（连续视频识别，推荐）

相机连续识别请用 `com.bonuos.facesdk.engine.FaceSession`，不要逐帧调用 `recognize(Bitmap)`。它给每张脸一个跟踪 ID，按"大小 → 越界 → 多人 → 稳定"的顺序做廉价过滤，活体三帧投票，特征在独立线程提取、每人只提 1～3 次，处于阈值边缘时融合多帧特征后再判定。示例见 `app` 模块的 `FaceRecognitionActivity`。

```java
FaceRecognizer recognizer = new FaceRecognizer(context);
MvEngines engines = new MvEngines(context);          // 检测 + 静默活体，共用一次原生格式转换
SessionConfig config = new SessionConfig();          // 阈值类参数均未校准，见类注释
FaceSession<FaceRepository.FaceUser> session = new FaceSession<>(config, engines, engines,
        new LegacyFeatureEngine(recognizer), recognizer, listener);

// CameraX 分析线程，每帧：
session.onFrame(new CameraFrame(nv21, width, height, imageInfo.getRotationDegrees(), timestampMs));

// 释放（在分析线程上，且在最后一帧之后）：
session.close(); engines.close(); recognizer.release();
```

`Listener` 三个回调都在调用 `onFrame` 的线程上：

| 回调 | 时机 |
|---|---|
| `onFaces(faces, frame)` | 每帧；每张脸的平滑框、活体状态、识别状态和原因码（如 `FACE_TOO_SMALL`、`NOT_STABLE`、`NOT_ALIVE`），用于画框和提示 |
| `onRecognized(event)` | 一个跟踪识别成功；同一人在 `identityCooldownMs` 内不重复上报 |
| `onRecognitionFailed(trackId, bestScore, reason)` | 一个跟踪用完重试次数仍未匹配，退避 `recognizeFailedRetryMs` 后重试 |

`CameraFrame` 的 `nv21` 缓冲在 `onFrame` 返回后即可复用；送给识别模型的是人脸周围的裁剪图（`featureCropScale`，默认框边长 2.2 倍），只转换裁剪区域。

### SyncManager

#### 构造方法
```java
SyncManager(Context context)
```

#### 主要方法

**syncData()**
- 说明: 从服务器同步人员数据到本地数据库

### LivenessDetector

#### 主要方法

**init(Context context)**
- 说明: 初始化活体检测

**detect(Bitmap bitmap, LivenessCallback callback)**
- 参数: 
  - `Bitmap` - 待检测的人脸图片
  - `LivenessCallback` - 回调接口
- 说明: 执行活体检测

## 权限要求

在 `AndroidManifest.xml` 中添加：

```xml
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.INTERNET" />
```

## 注意事项

1. SDK 仅支持 arm64-v8a 架构
2. 需要 Android 7.0 (API 24) 或更高版本
3. 首次运行会自动复制模型文件，需要一定时间
4. 人脸识别操作建议在后台线程执行

## 常见问题

### 库加载失败
确保所有 .so 文件都在 `jniLibs/arm64-v8a/` 目录下。

### 识别率低
- 调整识别阈值
- 确保照片质量良好
- 确保光线充足

### 同步失败
- 检查网络连接
- 检查服务器地址配置
- 检查服务器是否正常运行
