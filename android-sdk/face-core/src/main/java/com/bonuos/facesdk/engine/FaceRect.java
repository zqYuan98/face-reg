package com.bonuos.facesdk.engine;

/** Immutable axis-aligned box in upright frame pixels; right and bottom are exclusive. */
public final class FaceRect {
    public final float left, top, right, bottom;

    public FaceRect(float left, float top, float right, float bottom) {
        this.left = left;
        this.top = top;
        this.right = right;
        this.bottom = bottom;
    }

    public float width() { return right - left; }
    public float height() { return bottom - top; }
    public float centerX() { return (left + right) * 0.5f; }
    public float centerY() { return (top + bottom) * 0.5f; }
    public float area() { return Math.max(0f, width()) * Math.max(0f, height()); }

    public float iou(FaceRect other) {
        float w = Math.min(right, other.right) - Math.max(left, other.left);
        float h = Math.min(bottom, other.bottom) - Math.max(top, other.top);
        if (w <= 0 || h <= 0) return 0f;
        float inter = w * h;
        return inter / (area() + other.area() - inter);
    }

    /** Same centre, each side scaled by {@code factor}, then clipped to {@code [0,width) x [0,height)}. */
    public FaceRect scaledClipped(float factor, int width, int height) {
        float hw = width() * factor * 0.5f, hh = height() * factor * 0.5f;
        return new FaceRect(Math.max(0f, centerX() - hw), Math.max(0f, centerY() - hh),
                Math.min(width, centerX() + hw), Math.min(height, centerY() + hh));
    }

    /** Linear blend toward {@code target}; alpha 1 returns the target. */
    public FaceRect lerp(FaceRect target, float alpha) {
        return new FaceRect(left + (target.left - left) * alpha, top + (target.top - top) * alpha,
                right + (target.right - right) * alpha, bottom + (target.bottom - bottom) * alpha);
    }

    @Override public String toString() {
        return "[" + left + "," + top + "," + right + "," + bottom + "]";
    }
}
