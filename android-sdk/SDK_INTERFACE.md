> 2026-09-24 源码更新：同步 v2、配置 API、SQLite v3 和验证边界见 [SYNC_V2.md](SYNC_V2.md)。本文件下述历史 AAR/固定地址描述仅适用于旧版本；仓库旧 AAR 尚未重新发布。

# Face SDK 接口文档 (Java)

本文档详细说明了 `face-sdk-v1.0.0.aar` 提供的 Java 接口，供 Android 开发人员集成使用。

## 1. 核心类概览

| 类名 | 说明 |
| :--- | :--- |
| `com.bonuos.facesdk.FaceRecognizer` | **人脸识别核心类**。负责特征提取、人脸比对和识别。 |
| `com.bonuos.facesdk.LivenessDetector` | **活体检测类**。负责检测摄像头预览或图片中的人脸是否为活体。 |
| `com.bonuos.facesdk.SyncManager` | **数据同步管理类**。负责从服务器拉取最新的人脸数据库。 |
| `com.bonuos.facesdk.FaceRepository` | **本地数据库仓库**。提供对本地 SQLite 数据库的直接访问（通常不需要直接使用）。 |

---

## 2. 接口详解

### 2.1 FaceRecognizer (人脸识别)

负责加载本地人脸库并对输入的图像进行识别。

**构造方法**

```java
public FaceRecognizer(Context context)
```
- **参数**: `context` - 应用程序上下文。
- **说明**: 初始化 SDK 引擎，并从本地数据库加载所有人脸特征到内存缓存。

**方法**

#### recognize
```java
public RecognitionResult recognize(Bitmap bitmap)
```
- **说明**: 对输入图片进行人脸检测、特征提取，并与本地库进行比对。
- **参数**: `bitmap` - 包含人脸的图片（建议预处理为正向人脸）。
- **返回**: `RecognitionResult` 对象。

#### setThreshold
```java
public void setThreshold(float threshold)
```
- **说明**: 设置识别的相似度阈值。
- **参数**: `threshold` - 浮点数 (0.0 - 1.0)。默认约为 0.65，建议根据实际场景微调。

#### reloadUsers
```java
public synchronized void reloadUsers()
```
- **说明**: 重新从数据库加载用户数据到内存。
- **场景**: 当 `SyncManager` 完成数据同步后调用，以刷新内存缓存。

#### extractFeature
```java
public float[] extractFeature(Bitmap bitmap)
```
- **说明**: 仅提取人脸特征向量，不进行比对。
- **返回**: 特征数组 `float[]`，如果提取失败返回 `null`。

#### release
```java
public void release()
```
- **说明**: 释放底层算法引擎资源。建议在 `Activity.onDestroy()` 中调用。

---

### 2.2 LivenessDetector (活体检测)

基于 Silent-Face-Anti-Spoofing 模型的静默活体检测。

**方法**

#### init
```java
public void init(Context context)
```
- **说明**: 初始化活体检测模型。建议在子线程执行，因为加载模型可能耗时。

#### detect
```java
public LivenessResult detect(Bitmap bitmap)
```
- **说明**: 检测输入图片中的人脸活体得分。
- **参数**: `bitmap` - 待检测图片。
- **返回**: `LivenessResult` 对象。包含活体得分和人脸位置。

#### release
```java
public void release()
```
- **说明**: 释放模型资源。

---

### 2.3 SyncManager (数据同步)

负责与后端服务器通信，更新本地人脸库。

**构造方法**

```java
public SyncManager(Context context)
```

**方法**

#### syncData
```java
public void syncData()
```
- **说明**: 阻塞式方法（需要在子线程调用）。从服务器下载最新的用户列表和特征数据，并更新到本地数据库。
- **注意**: 若需修改服务器地址，请修改源码中的 `SYNC_URL` 常量或扩展该类。

---

## 3. 数据模型

### 3.1 RecognitionResult
识别结果封装类。

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `success` | `boolean` | 识别是否成功。 |
| `user` | `FaceRepository.FaceUser` | 识别到的用户信息（仅当 success=true 时有效）。 |
| `similarity` | `float` | 相似度得分 (0.0 - 1.0)。 |
| `message` | `String` | 错误信息或提示。 |

### 3.2 LivenessResult
活体检测结果。

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `score` | `float` | 活体得分。一般 > 0.9 可认为是活体，根据实际模型调整。 |
| `faceBox` | `FaceBox` | 检测到的人脸框坐标信息。 |

### 3.3 FaceRepository.FaceUser
单一用户实体。

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `userId` | `String` | 用户唯一 ID。 |
| `name` | `String` | 用户姓名。 |
| `feature` | `float[]` | 512维人脸特征向量。 |
| `featureVersion` | `String` | 特征版本号。 |

---

## 4. 集成示例

### 步骤 1: 引入 AAR
将 `face-sdk-v1.0.0.aar` 放入 `libs` 目录，并在 `build.gradle` 中配置：

> [!IMPORTANT]
> **关键依赖说明**
> 由于 AAR 以本地文件方式引入，无法自动解析传递性依赖。**必须**手动添加以下 Gson 和 WebSocket 库，否则运行时会发生 **Crash (ClassNotFoundException)**。

```kotlin
dependencies {
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar"))))
    
    // [必须] JSON 解析库
    implementation("com.google.code.gson:gson:2.10.1")
    
    // [必须] 数据同步需要的 WebSocket
    implementation("org.java-websocket:Java-WebSocket:1.5.4")
}
```

### 步骤 2: 完整调用流程

```java
public class FaceAuthActivity extends AppCompatActivity {
    private FaceRecognizer recognizer;
    private LivenessDetector livenessDetector;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        
        // 1. 初始化
        recognizer = new FaceRecognizer(this);
        livenessDetector = new LivenessDetector();
        livenessDetector.init(this);
        
        // 2. 开启线程进行识别
        new Thread(() -> {
            Bitmap captureInfo = getCameraFrame(); // 获取摄像头帧
            
            // 2.1 活体检测
            LivenessResult liveResult = livenessDetector.detect(captureInfo);
            if (liveResult.score > 0.9f) {
                
                // 2.2 人脸识别
                RecognitionResult result = recognizer.recognize(captureInfo);
                
                runOnUiThread(() -> {
                    if (result.success) {
                        Toast.makeText(this, "欢迎, " + result.user.name, Toast.LENGTH_SHORT).show();
                    } else {
                        Toast.makeText(this, "识别失败: " + result.message, Toast.LENGTH_SHORT).show();
                    }
                });
            }
        }).start();
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        // 3. 释放资源
        if (recognizer != null) recognizer.release();
        if (livenessDetector != null) livenessDetector.release();
    }
}
```
