import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Test-device sync provisioning, read from the git-ignored local.properties so no server address
// or device secret is committed. Empty values leave sync unconfigured, as before.
val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.isFile) file.inputStream().use { load(it) }
}
// Research-licensed InsightFace weights for evaluation builds: a local directory containing
// insightface/{det_500m,w600k_mbf,w600k_r50}.onnx. Never committed; see tools/engine-eval/README.md.
val insightFaceAssets: File? = localProperties.getProperty("insightface.assets")
    ?.takeIf { it.isNotBlank() }?.let { file(it) }
    ?.takeIf { File(it, "insightface/det_500m.onnx").isFile }

fun localString(key: String): String =
    "\"" + (localProperties.getProperty(key) ?: "").replace("\\", "\\\\").replace("\"", "\\\"") + "\""

// Device credentials are not embedded in APKs by default. A deliberately provisioned local test
// build must explicitly opt in; never enable this for a distributable artifact.
val includeLocalSync = findProperty("face.includeLocalSync") == "true"
fun syncString(key: String): String = if (includeLocalSync) localString(key) else "\"\""

android {
    // Models are mapped straight from the APK by the native engine; keep them uncompressed.
    androidResources {
        noCompress += "onnx"
        // The app runs Backend.NATIVE_INT8. The prebuilt library's models (fp32 recognizer, rotation
        // classifier, FSANet) stay in face-core for face-bench, but are not shipped here (~113 MB).
        // The fp32 IResNet-50 (174 MB) is evaluation-only; the app uses its int8 version.
        ignoreAssetsPattern = "!face_recognizer.onnx:!model_gray_mobilenetv2_rotcls.onnx:!fsanet-conv.onnx:!fsanet-var.onnx:" +
                "!w600k_r50.onnx:" +
                "!.svn:!.git:!.ds_store:!*.scc:.*:!CVS:!thumbs.db:!picasa.ini:!*~"
    }

    sourceSets {
        getByName("main") {
            if (insightFaceAssets != null) assets.srcDir(insightFaceAssets)
        }
    }

    packaging {
        jniLibs {
            // Only the prebuilt recognition library (FaceRecognizer.Backend.LEGACY_SDK) needs these.
            excludes += listOf("**/libface_sdk_jni.so", "**/libopencv_java4.so", "**/libonnxruntime4j_jni.so")
        }
    }
    namespace = "com.bonuos.faceapp"
    compileSdk = 34

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        buildConfigField("String", "SYNC_URL", syncString("face.sync.url"))
        buildConfigField("String", "SYNC_DEVICE_SN", syncString("face.sync.deviceSn"))
        buildConfigField("String", "SYNC_DEVICE_SECRET", syncString("face.sync.deviceSecret"))
        // insightface_cascade (default), insightface_r50_int8, insightface_mbf, native_int8, native
        buildConfigField("String", "FACE_ENGINE", localString("face.engine"))
        buildConfigField("boolean", "SHARE_FEATURE_CAPTURE", (findProperty("face.shareCapture") == "true").toString())
        buildConfigField("boolean", "EARLY_FRAME_ADMISSION", (findProperty("face.earlyAdmission") == "true").toString())
        buildConfigField("boolean", "SYNC_ALLOW_CLEARTEXT",
                (includeLocalSync && localProperties.getProperty("face.sync.allowCleartext") == "true").toString())
        applicationId = "com.bonuos.faceapp"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        
        ndk {
            abiFilters.add("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
    
    kotlinOptions {
        jvmTarget = "1.8"
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    
    implementation(project(":face-core"))
    
    // 手动补充 AAR 的依赖库 (因为本地 AAR 不会自动传递依赖)
    implementation("com.google.code.gson:gson:2.10.1")
    implementation("org.java-websocket:Java-WebSocket:1.5.4")

    val cameraxVersion = "1.3.1"
    implementation("androidx.camera:camera-core:${cameraxVersion}")
    implementation("androidx.camera:camera-camera2:${cameraxVersion}")
    implementation("androidx.camera:camera-lifecycle:${cameraxVersion}")
    implementation("androidx.camera:camera-view:${cameraxVersion}")

    implementation("com.github.bumptech.glide:glide:4.16.0")
    annotationProcessor("com.github.bumptech.glide:compiler:4.16.0")
}
