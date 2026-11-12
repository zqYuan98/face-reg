package com.bonuos.facesdk.engine;

import android.content.res.AssetManager;

/**
 * Silent liveness with its own models and frame conversion, for a worker thread separate from
 * detection. Score and close share a monitor: interrupting a worker does not stop native inference,
 * so release must wait for the current native call before freeing its models and frame buffers.
 */
final class NativeLiveness implements AutoCloseable, ScrfdEngines.LivenessWorker {
    static {
        System.loadLibrary("faceengine");
    }

    private long handle;

    NativeLiveness(AssetManager assets, String[] names, float[] scales, int[] sizes) {
        handle = nativeCreate(assets, names, scales, sizes);
        if (handle == 0) throw new IllegalStateException("Liveness models failed to load");
    }

    /** Sensor-orientation NV21 and an upright-frame minivision square (inclusive); NaN on failure. */
    @Override public synchronized float score(byte[] nv21, int width, int height, int rotation, int left, int top, int right, int bottom) {
        return handle == 0 ? Float.NaN : nativeScore(handle, nv21, width, height, rotation, left, top, right, bottom);
    }

    @Override
    public synchronized void close() {
        if (handle != 0) {
            nativeRelease(handle);
            handle = 0;
        }
    }

    private static native long nativeCreate(AssetManager assets, String[] names, float[] scales, int[] sizes);
    private static native void nativeRelease(long handle);
    private static native float nativeScore(long handle, byte[] nv21, int width, int height, int rotation,
                                            int left, int top, int right, int bottom);
}
