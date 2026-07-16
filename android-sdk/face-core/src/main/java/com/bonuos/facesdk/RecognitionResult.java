package com.bonuos.facesdk;

public class RecognitionResult {
    private boolean success;
    private String message;
    private FaceRepository.FaceUser user;
    private float similarity;

    public RecognitionResult(boolean success, String message) {
        this.success = success;
        this.message = message;
    }

    public RecognitionResult(boolean success, FaceRepository.FaceUser user, float similarity) {
        this.success = success;
        this.user = user;
        this.similarity = similarity;
        this.message = "识别成功";
    }

    public boolean isSuccess() {
        return success;
    }

    public String getMessage() {
        return message;
    }

    public FaceRepository.FaceUser getUser() {
        return user;
    }

    public float getSimilarity() {
        return similarity;
    }
}
