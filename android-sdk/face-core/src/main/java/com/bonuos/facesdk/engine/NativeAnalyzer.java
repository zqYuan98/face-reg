package com.bonuos.facesdk.engine;

import android.content.res.AssetManager;

import java.nio.ByteBuffer;

/** JNI handle for the native frame analyzer (SCRFD + silent liveness on one frame). Single thread. */
public final class NativeAnalyzer implements AutoCloseable, ScrfdEngines.Analyzer {
    static {
        System.loadLibrary("faceengine");
    }

    private long handle;
    private final int threads;

    NativeAnalyzer(AssetManager assets, String detectorAsset, int threads, String[] liveNames, float[] liveScales, int[] liveSizes) {
        if (threads < 1) throw new IllegalArgumentException("Positive detector thread count required");
        this.threads = threads;
        handle = nativeCreate(assets, detectorAsset, threads, liveNames, liveScales, liveSizes);
        if (handle == 0) throw new IllegalStateException("Native frame analyzer failed to load " + detectorAsset);
    }

    @Override public boolean setNv21(byte[] nv21, int width, int height, int rotation) {
        return handle != 0 && nativeSetNv21(handle, nv21, width, height, rotation);
    }

    /** Packed faces, 15 floats each: box (x1,y1,x2,y2), score, five points. */
    @Override public float[] detect(int longSide, float threshold) {
        return handle == 0 ? null : nativeDetect(handle, longSide, threshold);
    }

    /** Packed faces found inside an upright-frame region (any position), in frame coordinates. */
    @Override public float[] detectRegion(float x, float y, float w, float h, int longSide, float threshold) {
        return handle == 0 ? null : nativeDetectRegion(handle, x, y, w, h, longSide, threshold);
    }

    /**
     * Packs CameraX YUV_420_888 planes (direct buffers) into {@code out} as NV21 in native code:
     * one memcpy per row when the chroma planes are the usual interleaved VU buffer. Replaces the
     * per-pixel Java loop in Nv21.fromYuv420. Returns false when the arguments do not fit.
     */
    public static boolean packNv21(int width, int height, ByteBuffer y, int yRowStride, ByteBuffer u, ByteBuffer v,
                                   int uvRowStride, int uvPixelStride, byte[] out) {
        if (!y.isDirect() || !u.isDirect() || !v.isDirect()) return false;
        return nativePackNv21(width, height, y, yRowStride, u, v, uvRowStride, uvPixelStride, out);
    }

    @Override public float liveness(int left, int top, int right, int bottom) {
        return handle == 0 ? Float.NaN : nativeLiveness(handle, left, top, right, bottom);
    }

    @Override public int threads() { return threads; }

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
    private static native float[] nativeDetectRegion(long handle, float x, float y, float w, float h, int longSide, float threshold);
    private static native boolean nativePackNv21(int width, int height, ByteBuffer y, int yRowStride, ByteBuffer u,
                                                 ByteBuffer v, int uvRowStride, int uvPixelStride, byte[] out);
    private static native float nativeLiveness(long handle, int left, int top, int right, int bottom);
}
