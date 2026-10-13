package com.bonuos.facesdk.engine;

import com.bonuos.facesdk.image.Nv21;

/**
 * One camera frame in sensor orientation plus the rotation that makes it upright. All face
 * coordinates in the engine are upright-frame pixels. The caller may reuse {@link #nv21} after
 * {@link FaceSession#onFrame} returns; nothing holds on to it.
 */
public final class CameraFrame {
    public final byte[] nv21;
    public final int width, height;
    /** Clockwise rotation (0, 90, 180, 270) from sensor to upright, as CameraX reports it. */
    public final int rotationDegrees;
    public final long timestampMs;

    public CameraFrame(byte[] nv21, int width, int height, int rotationDegrees, long timestampMs) {
        if (nv21 == null || nv21.length < Nv21.size(width, height)) throw new IllegalArgumentException("NV21 buffer too small");
        int rotation = ((rotationDegrees % 360) + 360) % 360;
        if (rotation % 90 != 0) throw new IllegalArgumentException("Rotation must be a multiple of 90 degrees");
        this.nv21 = nv21;
        this.width = width;
        this.height = height;
        this.rotationDegrees = rotation;
        this.timestampMs = timestampMs;
    }

    public int uprightWidth() { return rotationDegrees % 180 == 0 ? width : height; }
    public int uprightHeight() { return rotationDegrees % 180 == 0 ? height : width; }
}
