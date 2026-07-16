package com.bonuos.facesdk;

import com.mv.engine.FaceBox;

public class LivenessResult {
    public float score;
    public FaceBox faceBox;

    public LivenessResult(float score, FaceBox faceBox) {
        this.score = score;
        this.faceBox = faceBox;
    }
}
