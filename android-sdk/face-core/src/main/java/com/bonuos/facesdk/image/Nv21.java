package com.bonuos.facesdk.image;

import java.nio.ByteBuffer;

/**
 * NV21 helpers that work directly on camera planes. They replace the JPEG round trip the
 * sample app used, which destroyed the high-frequency detail silent liveness depends on.
 */
public final class Nv21 {
    private Nv21() { }

    public static int size(int width, int height) {
        requireEven(width, height);
        return width * height * 3 / 2;
    }

    /**
     * Packs YUV_420_888 planes into tightly packed NV21, honouring row and pixel strides.
     * Plane order is Y, U, V as delivered by CameraX / Camera2.
     */
    public static void fromYuv420(int width, int height,
                                  ByteBuffer y, int yRowStride,
                                  ByteBuffer u, ByteBuffer v, int uvRowStride, int uvPixelStride,
                                  byte[] out) {
        int frame = width * height;
        if (out.length < size(width, height)) throw new IllegalArgumentException("Output buffer too small");
        if (yRowStride < width || uvPixelStride < 1 || uvRowStride < (width / 2 - 1) * uvPixelStride + 1)
            throw new IllegalArgumentException("Invalid plane strides");
        ByteBuffer yb = y.duplicate();
        if (yRowStride == width) {
            yb.position(0);
            yb.get(out, 0, frame);
        } else {
            for (int row = 0; row < height; row++) {
                yb.position(row * yRowStride);
                yb.get(out, row * width, width);
            }
        }
        int pos = frame;
        int chromaWidth = width / 2, chromaHeight = height / 2;
        for (int row = 0; row < chromaHeight; row++) {
            int base = row * uvRowStride;
            for (int col = 0; col < chromaWidth; col++) {
                int index = base + col * uvPixelStride;
                out[pos++] = v.get(index);
                out[pos++] = u.get(index);
            }
        }
    }

    /** Returns the frame rotated clockwise by 0, 90, 180 or 270 degrees. Width and height swap for 90/270. */
    public static byte[] rotate(byte[] src, int width, int height, int degrees) {
        int frame = width * height;
        if (src.length < size(width, height)) throw new IllegalArgumentException("Source buffer too small");
        int normalized = ((degrees % 360) + 360) % 360;
        byte[] dst = new byte[frame * 3 / 2];
        int cw = width / 2, ch = height / 2;
        switch (normalized) {
            case 0:
                System.arraycopy(src, 0, dst, 0, dst.length);
                return dst;
            case 90:
                for (int yy = 0; yy < height; yy++)
                    for (int xx = 0; xx < width; xx++)
                        dst[xx * height + (height - 1 - yy)] = src[yy * width + xx];
                for (int cy = 0; cy < ch; cy++)
                    for (int cx = 0; cx < cw; cx++) {
                        int s = frame + cy * width + cx * 2;
                        int d = frame + cx * height + (ch - 1 - cy) * 2;
                        dst[d] = src[s];
                        dst[d + 1] = src[s + 1];
                    }
                return dst;
            case 180:
                for (int yy = 0; yy < height; yy++)
                    for (int xx = 0; xx < width; xx++)
                        dst[(height - 1 - yy) * width + (width - 1 - xx)] = src[yy * width + xx];
                for (int cy = 0; cy < ch; cy++)
                    for (int cx = 0; cx < cw; cx++) {
                        int s = frame + cy * width + cx * 2;
                        int d = frame + (ch - 1 - cy) * width + (cw - 1 - cx) * 2;
                        dst[d] = src[s];
                        dst[d + 1] = src[s + 1];
                    }
                return dst;
            case 270:
                for (int yy = 0; yy < height; yy++)
                    for (int xx = 0; xx < width; xx++)
                        dst[(width - 1 - xx) * height + yy] = src[yy * width + xx];
                for (int cy = 0; cy < ch; cy++)
                    for (int cx = 0; cx < cw; cx++) {
                        int s = frame + cy * width + cx * 2;
                        int d = frame + (cw - 1 - cx) * height + cy * 2;
                        dst[d] = src[s];
                        dst[d + 1] = src[s + 1];
                    }
                return dst;
            default:
                throw new IllegalArgumentException("Rotation must be a multiple of 90 degrees");
        }
    }

    /** Full-range (JFIF) BT.601 NV21 to ARGB_8888 pixels, matching what YuvImage/JPEG decoding produced. */
    public static void toArgb(byte[] nv21, int width, int height, int[] out) {
        int frame = width * height;
        if (nv21.length < size(width, height) || out.length < frame) throw new IllegalArgumentException("Buffer too small");
        for (int row = 0; row < height; row++) {
            int uvRow = frame + (row >> 1) * width;
            for (int col = 0; col < width; col++) {
                int yValue = nv21[row * width + col] & 0xff;
                int uvIndex = uvRow + (col & ~1);
                int v = (nv21[uvIndex] & 0xff) - 128;
                int u = (nv21[uvIndex + 1] & 0xff) - 128;
                // 16.16 fixed point: 1.402, 0.344136, 0.714136, 1.772
                int r = yValue + ((91881 * v) >> 16);
                int g = yValue - ((22554 * u + 46802 * v) >> 16);
                int b = yValue + ((116130 * u) >> 16);
                out[row * width + col] = 0xff000000 | (clamp(r) << 16) | (clamp(g) << 8) | clamp(b);
            }
        }
    }

    /**
     * Converts one upright-frame rectangle of a sensor-orientation NV21 frame to ARGB, applying the
     * rotation on the fly. Same pixels as {@code toArgb(rotate(src, degrees))} cropped to the
     * rectangle, but touches only the crop: no full-frame rotation or conversion.
     *
     * @param degrees clockwise rotation that makes the sensor frame upright
     * @param left,top,cropWidth,cropHeight rectangle in upright coordinates, must lie inside the frame
     */
    public static void cropToArgb(byte[] src, int width, int height, int degrees,
                                  int left, int top, int cropWidth, int cropHeight, int[] out) {
        int rotation = normalizedRotation(degrees);
        int uprightWidth = rotation % 180 == 0 ? width : height, uprightHeight = rotation % 180 == 0 ? height : width;
        if (src.length < size(width, height) || out.length < cropWidth * cropHeight) throw new IllegalArgumentException("Buffer too small");
        if (left < 0 || top < 0 || cropWidth <= 0 || cropHeight <= 0 || left + cropWidth > uprightWidth || top + cropHeight > uprightHeight)
            throw new IllegalArgumentException("Crop outside frame");
        int frame = width * height, i = 0;
        for (int row = 0; row < cropHeight; row++) {
            int uy = top + row;
            for (int col = 0; col < cropWidth; col++) {
                int ux = left + col, sx, sy;
                switch (rotation) {
                    case 90: sx = uy; sy = height - 1 - ux; break;
                    case 180: sx = width - 1 - ux; sy = height - 1 - uy; break;
                    case 270: sx = width - 1 - uy; sy = ux; break;
                    default: sx = ux; sy = uy; break;
                }
                int yValue = src[sy * width + sx] & 0xff;
                int uvIndex = frame + (sy >> 1) * width + (sx & ~1);
                int v = (src[uvIndex] & 0xff) - 128;
                int u = (src[uvIndex + 1] & 0xff) - 128;
                int r = yValue + ((91881 * v) >> 16);
                int g = yValue - ((22554 * u + 46802 * v) >> 16);
                int b = yValue + ((116130 * u) >> 16);
                out[i++] = 0xff000000 | (clamp(r) << 16) | (clamp(g) << 8) | clamp(b);
            }
        }
    }

    /**
     * Nearest-neighbour samples a {@code gridWidth x gridHeight} luma grid over an upright-frame
     * rectangle (clipped to the frame). Used for cheap brightness and sharpness gates.
     */
    public static void sampleLuma(byte[] src, int width, int height, int degrees,
                                  float left, float top, float right, float bottom,
                                  int gridWidth, int gridHeight, float[] out) {
        int rotation = normalizedRotation(degrees);
        int uprightWidth = rotation % 180 == 0 ? width : height, uprightHeight = rotation % 180 == 0 ? height : width;
        if (out.length < gridWidth * gridHeight) throw new IllegalArgumentException("Buffer too small");
        float l = Math.max(0f, left), t = Math.max(0f, top);
        float r = Math.min(uprightWidth, right), b = Math.min(uprightHeight, bottom);
        float stepX = Math.max(0f, r - l) / gridWidth, stepY = Math.max(0f, b - t) / gridHeight;
        int i = 0;
        for (int gy = 0; gy < gridHeight; gy++) {
            int uy = Math.min(uprightHeight - 1, (int) (t + (gy + 0.5f) * stepY));
            for (int gx = 0; gx < gridWidth; gx++) {
                int ux = Math.min(uprightWidth - 1, (int) (l + (gx + 0.5f) * stepX)), sx, sy;
                switch (rotation) {
                    case 90: sx = uy; sy = height - 1 - ux; break;
                    case 180: sx = width - 1 - ux; sy = height - 1 - uy; break;
                    case 270: sx = width - 1 - uy; sy = ux; break;
                    default: sx = ux; sy = uy; break;
                }
                out[i++] = src[sy * width + sx] & 0xff;
            }
        }
    }

    private static int normalizedRotation(int degrees) {
        int normalized = ((degrees % 360) + 360) % 360;
        if (normalized % 90 != 0) throw new IllegalArgumentException("Rotation must be a multiple of 90 degrees");
        return normalized;
    }

    private static int clamp(int value) {
        return value < 0 ? 0 : (value > 255 ? 255 : value);
    }

    private static void requireEven(int width, int height) {
        if (width <= 0 || height <= 0 || (width & 1) != 0 || (height & 1) != 0)
            throw new IllegalArgumentException("NV21 dimensions must be positive and even");
    }
}
