// JNI for com.bonuos.facesdk.engine.NativeEngine.
#include <android/asset_manager_jni.h>
#include <cmath>
#include <cstring>
#include <android/bitmap.h>
#include <android/log.h>
#include <jni.h>
#include <opencv2/imgproc.hpp>
#include <memory>
#include <string>
#include "../image_ops.h"
#include "../pipeline_insightface.h"
#include "../pipeline_v1.h"

#define LOG_ERR(...) __android_log_print(ANDROID_LOG_ERROR, "FaceEngine", __VA_ARGS__)

using namespace face;

namespace {

std::unique_ptr<NetSession> LoadAsset(AAssetManager *assets, const std::string &name, const OrtConfig &config) {
    AAsset *asset = AAssetManager_open(assets, name.c_str(), AASSET_MODE_BUFFER);
    if (asset == nullptr) throw std::runtime_error("missing asset " + name);
    const void *data = AAsset_getBuffer(asset);
    const off_t size = AAsset_getLength(asset);
    std::unique_ptr<NetSession> session;
    try {
        if (data == nullptr) throw std::runtime_error("unreadable asset " + name);
        session.reset(new NetSession(data, static_cast<size_t>(size), config));
    } catch (...) {
        AAsset_close(asset);
        throw;
    }
    AAsset_close(asset);  // ORT keeps its own copy of the weights
    return session;
}

jfloatArray ToJava(JNIEnv *env, const ExtractResult &result, jfloatArray out_landmarks) {
    if (result.feature.empty()) return nullptr;
    if (out_landmarks != nullptr && env->GetArrayLength(out_landmarks) >= 10) {
        float points[10];
        for (int k = 0; k < 5; ++k) {
            points[2 * k] = static_cast<float>(result.landmarks[k].x);
            points[2 * k + 1] = static_cast<float>(result.landmarks[k].y);
        }
        env->SetFloatArrayRegion(out_landmarks, 0, 10, points);
    }
    jfloatArray feature = env->NewFloatArray(static_cast<jsize>(result.feature.size()));
    if (feature != nullptr) env->SetFloatArrayRegion(feature, 0, static_cast<jsize>(result.feature.size()), result.feature.data());
    return feature;
}

LandmarkMode Mode(jint mode) {
    return mode == 1 ? LandmarkMode::kSubpixel : mode == 2 ? LandmarkMode::kTta : LandmarkMode::kTruncated;
}

}  // namespace

extern "C" {

static std::string ToString(JNIEnv *env, jstring value) {
    if (value == nullptr) return std::string();
    const char *chars = env->GetStringUTFChars(value, nullptr);
    std::string result(chars);
    env->ReleaseStringUTFChars(value, chars);
    return result;
}

/** kind 0: recognizer_v1_1024 (detector asset optional, for still images); kind 1: InsightFace. */
JNIEXPORT jlong JNICALL
Java_com_bonuos_facesdk_engine_NativeEngine_nativeCreate(JNIEnv *env, jclass, jobject asset_manager, jint kind,
                                                        jstring recognizer_asset, jstring detector_asset, jint threads) {
    AAssetManager *assets = AAssetManager_fromJava(env, asset_manager);
    const std::string recognizer = ToString(env, recognizer_asset), detector = ToString(env, detector_asset);
    try {
        OrtConfig config;
        config.threads = threads;
        OrtConfig small = config;
        small.threads = 1;  // the landmark nets take ~1 ms; a thread pool only adds wake-up latency
        FacePipeline *pipeline;
        if (kind == 1) {
            pipeline = new PipelineInsightFace(LoadAsset(assets, detector, config), LoadAsset(assets, recognizer, config));
        } else {
            pipeline = new PipelineV1(LoadAsset(assets, "face_landmarker_pts5_net1.onnx", small),
                                      LoadAsset(assets, "face_landmarker_pts5_net2.onnx", small),
                                      LoadAsset(assets, recognizer, config),
                                      detector.empty() ? nullptr : LoadAsset(assets, detector, config));
        }
        return reinterpret_cast<jlong>(pipeline);
    } catch (const std::exception &error) {
        LOG_ERR("engine init failed: %s", error.what());
        return 0;
    }
}

JNIEXPORT void JNICALL
Java_com_bonuos_facesdk_engine_NativeEngine_nativeRelease(JNIEnv *, jclass, jlong handle) {
    delete reinterpret_cast<FacePipeline *>(handle);
}

JNIEXPORT jboolean JNICALL
Java_com_bonuos_facesdk_engine_NativeEngine_nativeCropBgr(JNIEnv *env, jclass, jbyteArray nv21, jint width, jint height,
                                                         jint rotation, jint left, jint top, jint crop_width,
                                                         jint crop_height, jbyteArray out) {
    if (nv21 == nullptr || out == nullptr || width <= 0 || height <= 0 || crop_width <= 0 || crop_height <= 0 ||
        env->GetArrayLength(nv21) < width * height * 3 / 2 || env->GetArrayLength(out) < crop_width * crop_height * 3)
        return JNI_FALSE;
    jbyte *src = env->GetByteArrayElements(nv21, nullptr);
    jbyte *dst = env->GetByteArrayElements(out, nullptr);
    cv::Mat view(crop_height, crop_width, CV_8UC3, dst);
    cv::Mat converted;
    bool ok = Nv21CropToBgr(reinterpret_cast<uint8_t *>(src), width, height, rotation, left, top, crop_width,
                            crop_height, converted);
    if (ok) converted.copyTo(view);
    env->ReleaseByteArrayElements(nv21, src, JNI_ABORT);
    env->ReleaseByteArrayElements(out, dst, ok ? 0 : JNI_ABORT);
    return ok ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jfloatArray JNICALL
Java_com_bonuos_facesdk_engine_NativeEngine_nativeExtractBgr(JNIEnv *env, jclass, jlong handle, jbyteArray bgr,
                                                            jint width, jint height, jfloat x1, jfloat y1, jfloat x2,
                                                            jfloat y2, jint mode, jfloatArray out_landmarks) {
    auto *pipeline = reinterpret_cast<FacePipeline *>(handle);
    if (pipeline == nullptr || bgr == nullptr || width <= 0 || height <= 0 ||
        env->GetArrayLength(bgr) < width * height * 3)
        return nullptr;
    jbyte *data = env->GetByteArrayElements(bgr, nullptr);
    jfloatArray result = nullptr;
    try {
        cv::Mat image(height, width, CV_8UC3, data);
        result = ToJava(env, pipeline->FromBox(image, DetectedBox{x1, y1, x2, y2, 1.f}, Mode(mode)), out_landmarks);
    } catch (const std::exception &error) {
        LOG_ERR("extract failed: %s", error.what());
    }
    env->ReleaseByteArrayElements(bgr, data, JNI_ABORT);
    return result;
}

JNIEXPORT jfloatArray JNICALL
Java_com_bonuos_facesdk_engine_NativeEngine_nativeExtractBitmap(JNIEnv *env, jclass, jlong handle, jobject bitmap,
                                                               jint mode, jfloatArray out_landmarks) {
    auto *pipeline = reinterpret_cast<FacePipeline *>(handle);
    AndroidBitmapInfo info;
    void *pixels = nullptr;
    if (pipeline == nullptr || AndroidBitmap_getInfo(env, bitmap, &info) < 0 ||
        info.format != ANDROID_BITMAP_FORMAT_RGBA_8888 || AndroidBitmap_lockPixels(env, bitmap, &pixels) < 0)
        return nullptr;
    cv::Mat bgr;
    cv::cvtColor(cv::Mat(static_cast<int>(info.height), static_cast<int>(info.width), CV_8UC4, pixels, info.stride),
                 bgr, cv::COLOR_RGBA2BGR);
    AndroidBitmap_unlockPixels(env, bitmap);
    try {
        return ToJava(env, pipeline->FromImage(bgr, Mode(mode)), out_landmarks);
    } catch (const std::exception &error) {
        LOG_ERR("extract failed: %s", error.what());
        return nullptr;
    }
}

}  // extern "C"

// ---------------------------------------------------------------- NativeAnalyzer

#include "../frame_analyzer.h"

extern "C" {

JNIEXPORT jlong JNICALL
Java_com_bonuos_facesdk_engine_NativeAnalyzer_nativeCreate(JNIEnv *env, jclass, jobject asset_manager, jstring detector_asset,
                                                          jint threads, jobjectArray live_names, jfloatArray live_scales,
                                                          jintArray live_sizes) {
    AAssetManager *assets = AAssetManager_fromJava(env, asset_manager);
    try {
        OrtConfig config;
        config.threads = threads;
        const jsize count = env->GetArrayLength(live_names);
        std::vector<float> scales(static_cast<size_t>(count));
        std::vector<jint> sizes(static_cast<size_t>(count));
        env->GetFloatArrayRegion(live_scales, 0, count, scales.data());
        env->GetIntArrayRegion(live_sizes, 0, count, sizes.data());
        std::vector<ModelConfig> configs;
        for (jsize i = 0; i < count; ++i) {
            ModelConfig c;
            c.name = ToString(env, static_cast<jstring>(env->GetObjectArrayElement(live_names, i)));
            c.scale = scales[static_cast<size_t>(i)];
            c.width = c.height = sizes[static_cast<size_t>(i)];
            c.shift_x = c.shift_y = 0.f;
            c.org_resize = false;
            configs.push_back(c);
        }
        return reinterpret_cast<jlong>(new FrameAnalyzer(LoadAsset(assets, ToString(env, detector_asset), config), assets, configs));
    } catch (const std::exception &error) {
        LOG_ERR("analyzer init failed: %s", error.what());
        return 0;
    }
}

JNIEXPORT void JNICALL
Java_com_bonuos_facesdk_engine_NativeAnalyzer_nativeRelease(JNIEnv *, jclass, jlong handle) {
    delete reinterpret_cast<FrameAnalyzer *>(handle);
}

JNIEXPORT jboolean JNICALL
Java_com_bonuos_facesdk_engine_NativeAnalyzer_nativeSetNv21(JNIEnv *env, jclass, jlong handle, jbyteArray nv21,
                                                           jint width, jint height, jint rotation) {
    auto *analyzer = reinterpret_cast<FrameAnalyzer *>(handle);
    if (analyzer == nullptr || nv21 == nullptr || env->GetArrayLength(nv21) < width * height * 3 / 2) return JNI_FALSE;
    // Critical access: the analyzer makes the only copy (a plain memcpy, no allocation or JNI calls inside).
    void *data = env->GetPrimitiveArrayCritical(nv21, nullptr);
    if (data == nullptr) return JNI_FALSE;
    const bool ok = analyzer->SetNv21(static_cast<uint8_t *>(data), width, height, rotation);
    env->ReleasePrimitiveArrayCritical(nv21, data, JNI_ABORT);
    return ok ? JNI_TRUE : JNI_FALSE;
}

/** Packed per face: x1 y1 x2 y2 score, then 10 keypoint coordinates (15 floats). */
static jfloatArray PackFaces(JNIEnv *env, const std::vector<ScrfdFace> &faces) {
    std::vector<float> packed;
    for (const ScrfdFace &f : faces) {
        packed.insert(packed.end(), {f.box.x1, f.box.y1, f.box.x2, f.box.y2, f.box.score});
        for (const auto &p : f.kps) {
            packed.push_back(static_cast<float>(p.x));
            packed.push_back(static_cast<float>(p.y));
        }
    }
    jfloatArray result = env->NewFloatArray(static_cast<jsize>(packed.size()));
    if (result != nullptr && !packed.empty()) env->SetFloatArrayRegion(result, 0, static_cast<jsize>(packed.size()), packed.data());
    return result;
}

JNIEXPORT jfloatArray JNICALL
Java_com_bonuos_facesdk_engine_NativeAnalyzer_nativeDetect(JNIEnv *env, jclass, jlong handle, jint long_side, jfloat threshold) {
    auto *analyzer = reinterpret_cast<FrameAnalyzer *>(handle);
    if (analyzer == nullptr) return nullptr;
    try {
        return PackFaces(env, analyzer->Detect(long_side, threshold));
    } catch (const std::exception &error) {
        LOG_ERR("detect failed: %s", error.what());
        return nullptr;
    }
}

JNIEXPORT jfloatArray JNICALL
Java_com_bonuos_facesdk_engine_NativeAnalyzer_nativeDetectRegion(JNIEnv *env, jclass, jlong handle, jfloat x, jfloat y,
                                                                jfloat w, jfloat h, jint long_side, jfloat threshold) {
    auto *analyzer = reinterpret_cast<FrameAnalyzer *>(handle);
    if (analyzer == nullptr) return nullptr;
    try {
        return PackFaces(env, analyzer->DetectRegion(x, y, w, h, long_side, threshold));
    } catch (const std::exception &error) {
        LOG_ERR("region detect failed: %s", error.what());
        return nullptr;
    }
}

/**
 * Packs CameraX YUV_420_888 planes (direct buffers) into NV21. When the V and U planes are the
 * usual interleaved view of one VU buffer (pixel stride 2, U one byte after V), each chroma row is
 * a single memcpy; otherwise it falls back to a per-pixel copy. Returns false on bad arguments.
 */
JNIEXPORT jboolean JNICALL
Java_com_bonuos_facesdk_engine_NativeAnalyzer_nativePackNv21(JNIEnv *env, jclass, jint width, jint height,
                                                            jobject y, jint y_row_stride, jobject u, jobject v,
                                                            jint uv_row_stride, jint uv_pixel_stride, jbyteArray out) {
    if (width <= 0 || height <= 0 || (width & 1) || (height & 1) || out == nullptr) return JNI_FALSE;
    auto *yp = static_cast<const uint8_t *>(env->GetDirectBufferAddress(y));
    auto *up = static_cast<const uint8_t *>(env->GetDirectBufferAddress(u));
    auto *vp = static_cast<const uint8_t *>(env->GetDirectBufferAddress(v));
    const jlong ycap = env->GetDirectBufferCapacity(y), ucap = env->GetDirectBufferCapacity(u), vcap = env->GetDirectBufferCapacity(v);
    const int cw = width / 2, ch = height / 2;
    if (yp == nullptr || up == nullptr || vp == nullptr || y_row_stride < width || uv_pixel_stride < 1
        || ycap < static_cast<jlong>(y_row_stride) * (height - 1) + width
        || ucap < static_cast<jlong>(uv_row_stride) * (ch - 1) + static_cast<jlong>(uv_pixel_stride) * (cw - 1) + 1
        || vcap < static_cast<jlong>(uv_row_stride) * (ch - 1) + static_cast<jlong>(uv_pixel_stride) * (cw - 1) + 1
        || env->GetArrayLength(out) < width * height * 3 / 2)
        return JNI_FALSE;
    jbyte *dst = static_cast<jbyte *>(env->GetPrimitiveArrayCritical(out, nullptr));
    if (dst == nullptr) return JNI_FALSE;
    auto *o = reinterpret_cast<uint8_t *>(dst);
    for (int r = 0; r < height; ++r) std::memcpy(o + r * width, yp + static_cast<size_t>(r) * y_row_stride, width);
    uint8_t *c = o + width * height;
    const bool interleaved = uv_pixel_stride == 2 && up == vp + 1;
    for (int r = 0; r < ch; ++r) {
        const uint8_t *vr = vp + static_cast<size_t>(r) * uv_row_stride, *ur = up + static_cast<size_t>(r) * uv_row_stride;
        uint8_t *dr = c + r * width;
        if (interleaved) {
            std::memcpy(dr, vr, width - 1);  // V0 U0 V1 U1 ... V(n-1); the last U lies outside V's view
            dr[width - 1] = ur[(cw - 1) * 2];
        } else {
            for (int i = 0; i < cw; ++i) {
                dr[2 * i] = vr[i * uv_pixel_stride];
                dr[2 * i + 1] = ur[i * uv_pixel_stride];
            }
        }
    }
    env->ReleasePrimitiveArrayCritical(out, dst, 0);
    return JNI_TRUE;
}

JNIEXPORT jfloat JNICALL
Java_com_bonuos_facesdk_engine_NativeAnalyzer_nativeLiveness(JNIEnv *, jclass, jlong handle, jint left, jint top,
                                                            jint right, jint bottom) {
    auto *analyzer = reinterpret_cast<FrameAnalyzer *>(handle);
    return analyzer == nullptr ? NAN : analyzer->Liveness(left, top, right, bottom);
}

}  // extern "C"
