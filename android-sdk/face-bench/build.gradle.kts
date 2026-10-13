import java.util.Properties

plugins {
    id("com.android.application")
}

// The ArcSoft SDK is licensed per developer and must never be committed. Point the build at a
// local copy with -Parcsoft.sdk.libs=<dir> or arcsoft.sdk.libs=<dir> in android-sdk/local.properties.
// The directory is ArcSoft's "libs" folder: arcsoft_face.jar plus arm64-v8a/ and armeabi-v7a/.
val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.isFile) file.inputStream().use { load(it) }
}
val arcsoftLibs: File? = ((findProperty("arcsoft.sdk.libs") as String?) ?: localProperties.getProperty("arcsoft.sdk.libs"))
    ?.takeIf { it.isNotBlank() }
    ?.let { file(it) }
    ?.takeIf { File(it, "arcsoft_face.jar").isFile && File(it, "arm64-v8a").isDirectory }

// Research-licensed InsightFace weights (local only), same property as the app.
val insightFaceAssets: File? = localProperties.getProperty("insightface.assets")
    ?.takeIf { it.isNotBlank() }?.let { file(it) }
    ?.takeIf { File(it, "insightface/det_500m.onnx").isFile }

android {
    // Models are mapped straight from the APK by the native engine; keep them uncompressed.
    androidResources {
        noCompress += "onnx"
    }
    namespace = "com.bonuos.facebench"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.bonuos.facebench"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1"
        ndk {
            abiFilters.add("arm64-v8a")
        }
        buildConfigField("boolean", "ARCSOFT_AVAILABLE", (arcsoftLibs != null).toString())
        buildConfigField("boolean", "INSIGHTFACE_AVAILABLE", (insightFaceAssets != null).toString())
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    sourceSets {
        getByName("main") {
            if (arcsoftLibs != null) {
                java.srcDir("src/arcsoft/java")
                jniLibs.srcDir(arcsoftLibs)
            }
            if (insightFaceAssets != null) assets.srcDir(insightFaceAssets)
        }
    }

    packaging {
        jniLibs {
            pickFirsts += "**/libc++_shared.so"
        }
    }
}

dependencies {
    implementation(project(":face-core"))
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.code.gson:gson:2.10.1")

    val cameraxVersion = "1.3.1"
    implementation("androidx.camera:camera-core:$cameraxVersion")
    implementation("androidx.camera:camera-camera2:$cameraxVersion")
    implementation("androidx.camera:camera-lifecycle:$cameraxVersion")
    implementation("androidx.camera:camera-view:$cameraxVersion")

    if (arcsoftLibs != null) {
        implementation(files(File(arcsoftLibs, "arcsoft_face.jar")))
    }

    testImplementation("junit:junit:4.13.2")
}
