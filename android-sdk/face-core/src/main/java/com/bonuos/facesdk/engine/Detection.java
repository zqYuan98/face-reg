package com.bonuos.facesdk.engine;

/** One detector output in upright frame coordinates. */
public final class Detection {
    public final FaceRect rect;
    public final float score;

    public Detection(FaceRect rect, float score) {
        this.rect = rect;
        this.score = score;
    }
}
