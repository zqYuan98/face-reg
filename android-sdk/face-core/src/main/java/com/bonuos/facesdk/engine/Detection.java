package com.bonuos.facesdk.engine;

/** One detector output in upright frame coordinates. */
public final class Detection {
    public final FaceRect rect;
    public final float score;
    /** Five points (eyes, nose, mouth corners) as x0,y0..x4,y4 when the detector provides them, else null. */
    public final float[] keypoints;

    public Detection(FaceRect rect, float score) {
        this(rect, score, null);
    }

    public Detection(FaceRect rect, float score, float[] keypoints) {
        this.rect = rect;
        this.score = score;
        this.keypoints = keypoints;
    }
}
