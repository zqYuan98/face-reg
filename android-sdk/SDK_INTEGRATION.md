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
- 说明: 设置识别阈值，默认 0.80

**release()**
- 说明: 释放资源

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
