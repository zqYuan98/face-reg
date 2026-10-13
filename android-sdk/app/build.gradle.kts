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
fun localString(key: String): String =
    "\"" + (localProperties.getProperty(key) ?: "").replace("\\", "\\\\").replace("\"", "\\\"") + "\""

android {
    // Models are mapped straight from the APK by the native engine; keep them uncompressed.
    androidResources {
        noCompress += "onnx"
        // The app runs Backend.NATIVE_INT8. The prebuilt library's models (fp32 recognizer, rotation
        // classifier, FSANet) stay in face-core for face-bench, but are not shipped here (~113 MB).
        ignoreAssetsPattern = "!face_recognizer.onnx:!model_gray_mobilenetv2_rotcls.onnx:!fsanet-conv.onnx:!fsanet-var.onnx:" +
                "!.svn:!.git:!.ds_store:!*.scc:.*:!CVS:!thumbs.db:!picasa.ini:!*~"
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
        buildConfigField("String", "SYNC_URL", localString("face.sync.url"))
        buildConfigField("String", "SYNC_DEVICE_SN", localString("face.sync.deviceSn"))
        buildConfigField("String", "SYNC_DEVICE_SECRET", localString("face.sync.deviceSecret"))
        buildConfigField("boolean", "SYNC_ALLOW_CLEARTEXT",
                (localProperties.getProperty("face.sync.allowCleartext") == "true").toString())
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
