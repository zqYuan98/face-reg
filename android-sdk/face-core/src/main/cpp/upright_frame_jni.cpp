#include <jni.h>
#include <opencv2/imgproc.hpp>
#include "upright_frame.h"
#include "android_log.h"

extern "C" {

JNIEXPORT jlong JNICALL
Java_com_mv_engine_UprightFrame_nativeAllocate(JNIEnv *, jclass) {
    return reinterpret_cast<jlong>(new UprightFrame());
}

JNIEXPORT void JNICALL
Java_com_mv_engine_UprightFrame_nativeRelease(JNIEnv *, jclass, jlong handle) {
    delete reinterpret_cast<UprightFrame *>(handle);
}

/**
 * NV21 in sensor orientation -> upright BGR. OpenCV's NV21 conversion uses one chroma sample per
 * 2x2 block, so converting then rotating gives exactly what rotating the NV21 first gave.
 * Returns 0 on success.
 */
JNIEXPORT jint JNICALL
Java_com_mv_engine_UprightFrame_nativeSetNv21(JNIEnv *env, jclass, jlong handle, jbyteArray nv21,
                                             jint width, jint height, jint rotation_degrees) {
    auto *frame = reinterpret_cast<UprightFrame *>(handle);
    if (frame == nullptr || nv21 == nullptr || width <= 0 || height <= 0 || (width & 1) || (height & 1))
        return -1;
    if (env->GetArrayLength(nv21) < width * height * 3 / 2) return -2;
    int rotation = ((rotation_degrees % 360) + 360) % 360;
    if (rotation % 90 != 0) return -3;

    jbyte *data = env->GetByteArrayElements(nv21, nullptr);
    if (data == nullptr) return -4;
    cv::Mat yuv(height + height / 2, width, CV_8UC1, reinterpret_cast<unsigned char *>(data));
    if (rotation == 0) {
        cv::cvtColor(yuv, frame->bgr, cv::COLOR_YUV2BGR_NV21);
    } else {
        cv::cvtColor(yuv, frame->sensor, cv::COLOR_YUV2BGR_NV21);
        int code = rotation == 90 ? cv::ROTATE_90_CLOCKWISE
                 : rotation == 180 ? cv::ROTATE_180 : cv::ROTATE_90_COUNTERCLOCKWISE;
        cv::rotate(frame->sensor, frame->bgr, code);
    }
    // Read-only access: JNI_ABORT skips copying the buffer back into the Java array.
    env->ReleaseByteArrayElements(nv21, data, JNI_ABORT);
    return 0;
}

}
