package com.bonuos.facesdk.engine;

import android.content.res.AssetManager;

/** JNI handle for the native frame analyzer (SCRFD + silent liveness on one converted frame). Single thread. */
final class NativeAnalyzer implements AutoCloseable {
    static {
        System.loadLibrary("faceengine");
    }

    private long handle;

    NativeAnalyzer(AssetManager assets, String detectorAsset, int threads, String[] liveNames, float[] liveScales, int[] liveSizes) {
        handle = nativeCreate(assets, detectorAsset, threads, liveNames, liveScales, liveSizes);
        if (handle == 0) throw new IllegalStateException("Native frame analyzer failed to load " + detectorAsset);
    }

    boolean setNv21(byte[] nv21, int width, int height, int rotation) {
        return handle != 0 && nativeSetNv21(handle, nv21, width, height, rotation);
    }

    /** Packed faces, 15 floats each: box (x1,y1,x2,y2), score, five points. */
    float[] detect(int longSide, float threshold) {
        return handle == 0 ? null : nativeDetect(handle, longSide, threshold);
    }

    float liveness(int left, int top, int right, int bottom) {
        return handle == 0 ? Float.NaN : nativeLiveness(handle, left, top, right, bottom);
    }

    @Override
    public void close() {
        if (handle != 0) {
            nativeRelease(handle);
            handle = 0;
        }
    }

    private static native long nativeCreate(AssetManager assets, String detectorAsset, int threads,
                                            String[] liveNames, float[] liveScales, int[] liveSizes);
    private static native void nativeRelease(long handle);
    private static native boolean nativeSetNv21(long handle, byte[] nv21, int width, int height, int rotation);
    private static native float[] nativeDetect(long handle, int longSide, float threshold);
    private static native float nativeLiveness(long handle, int left, int top, int right, int bottom);
}
