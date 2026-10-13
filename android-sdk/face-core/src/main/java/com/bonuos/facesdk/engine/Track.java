package com.bonuos.facesdk.engine;

/** A face followed across frames. Mutated only by {@link FaceTracker} on the analysis thread. */
public final class Track {
    public final long id;
    /** Latest raw detector box; what the models see. */
    FaceRect rect;
    /** Exponentially smoothed box for drawing only. */
    FaceRect smoothed;
    /** Latest detector keypoints (x0,y0..x4,y4), or null when the detector gives none. */
    float[] keypoints;
    float score;
    int missed;
    int hits;
    /** Ring buffer of recent centres for the stability filter. */
    private final float[] centerX, centerY;
    private int historySize, historyHead;

    Track(long id, Detection detection, int history) {
        this.id = id;
        this.rect = detection.rect;
        this.smoothed = detection.rect;
        this.score = detection.score;
        this.keypoints = detection.keypoints;
        this.centerX = new float[history];
        this.centerY = new float[history];
        record();
    }

    public FaceRect rect() { return rect; }
    public FaceRect smoothedRect() { return smoothed; }
    public float[] keypoints() { return keypoints; }
    /** Frames this track has been matched, including the first. */
    public int hits() { return hits; }
    /** True when the track was matched in the latest frame (lost tracks are kept for a few frames). */
    public boolean visible() { return missed == 0; }

    void record() {
        hits++;
        centerX[historyHead] = rect.centerX();
        centerY[historyHead] = rect.centerY();
        historyHead = (historyHead + 1) % centerX.length;
        if (historySize < centerX.length) historySize++;
    }

    /**
     * Largest centre jump between consecutive frames over the full history, or +inf until the
     * history is full. Same idea as ArcSoft's move limit: a face must hold still for N frames.
     */
    public float maxRecentMove() {
        int n = centerX.length;
        if (historySize < n) return Float.POSITIVE_INFINITY;
        float max = 0f;
        for (int k = 1; k < n; k++) {
            int a = (historyHead + k - 1) % n, b = (historyHead + k) % n;
            float dx = centerX[b] - centerX[a], dy = centerY[b] - centerY[a];
            max = Math.max(max, (float) Math.sqrt(dx * dx + dy * dy));
        }
        return max;
    }
}
