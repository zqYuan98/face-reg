package com.bonuos.facesdk.engine;

/**
 * Tunables for {@link FaceSession}. Defaults are starting points: every threshold marked
 * "calibrate" must be replaced by a value measured on the parity rig before it is relied on.
 * The session copies the config when it is created.
 */
public final class SessionConfig implements Cloneable {
    // Tracking
    public float trackMinIou = 0.3f;
    public int trackMaxMissed = 3;
    public float boxSmoothing = 0.5f;
    public long firstTrackId = 1;

    // Cheap filters, in cost order
    /** Shorter box side in upright pixels; smaller faces get FACE_TOO_SMALL. */
    public int minFaceSize = 80;
    /** A box may stick out of the frame by this fraction of its size before BEYOND_BOUNDARY. */
    public float boundaryTolerance = 0.05f;
    /** Frames a face must hold still before any model runs on it. */
    public int stableFrames = 5;
    /** Max centre move between consecutive frames, as a fraction of the box width. */
    public float moveLimitRatio = 0.08f;
    /** Process only the largest face. */
    public boolean keepMaxFace = false;
    /** Refuse to decide while more than one face is visible (identity verification). */
    public boolean strictSingleFace = false;
    /**
     * Pose gate from the detector's five points, before liveness or extraction (degrees, Pose5
     * scale). Only applies when the detector returns keypoints; 30/25 match the enrolment service.
     */
    public float maxYaw = 30f, maxPitch = 25f;

    // Power
    /**
     * When nothing needs work (no face, or every face recognised or backing off), analyse at most
     * one frame per this interval. Detection resumes at full rate as soon as a face needs work.
     */
    public long idleFrameIntervalMs = 100;

    // Image quality, measured on the face luma before a crop goes to the recognizer
    public float minBrightness = 40f, maxBrightness = 235f;
    /** Absolute Laplacian-variance floor; 0 disables (calibrate). */
    public float minSharpness = 0f;
    /**
     * Relative sharpness gate: a frame is used only if it is at least this fraction of the
     * sharpest frame seen on the same track. Needs no calibration; skips motion-blurred frames.
     */
    public float relativeSharpness = 0.6f;

    // Liveness
    /** Silent-liveness score threshold (calibrate; 0.7 is the shipped, uncalibrated value). */
    public float livenessThreshold = 0.7f;
    public int livenessWindow = 3;
    public int livenessRequired = 2;
    public long livenessFailedRetryMs = 1000;
    /** When false the session never runs liveness and treats every face as live (testing only). */
    public boolean livenessEnabled = true;

    // Recognition
    /** Accept at or above this score (calibrate; 0.65 is the SDK's historical default). */
    public float thresholdHigh = 0.65f;
    /** Between low and high the next frames are fused before deciding (calibrate). */
    public float thresholdLow = 0.60f;
    /** Required lead over the runner-up; 0 disables (calibrate for large galleries). */
    public float topMargin = 0f;
    /** Max frames averaged into one probe. */
    public int fusionFrames = 3;
    /** Failed attempts before the track backs off. */
    public int extractRetryCount = 3;
    public long recognizeFailedRetryMs = 1000;
    /** Each further failure of the same track doubles the back-off, up to this (a stranger standing still). */
    public long recognizeFailedRetryMaxMs = 8000;
    /** The same person is reported again only after this long, even on a new track. */
    public long identityCooldownMs = 5000;
    /** Crop sent to the recognizer: box side times this factor, clipped to the frame. 0 = whole frame. */
    public float featureCropScale = 2.2f;

    @Override public SessionConfig clone() {
        try {
            return (SessionConfig) super.clone();
        } catch (CloneNotSupportedException e) {
            throw new AssertionError(e);
        }
    }

    void validate() {
        if (minFaceSize < 1 || stableFrames < 2 || !(moveLimitRatio > 0) || livenessWindow < 1
                || livenessRequired < 1 || livenessRequired > livenessWindow || fusionFrames < 1 || extractRetryCount < 1
                || thresholdLow > thresholdHigh || topMargin < 0 || relativeSharpness < 0 || relativeSharpness > 1
                || minBrightness > maxBrightness || !(maxYaw > 0) || !(maxPitch > 0) || idleFrameIntervalMs < 0
                || recognizeFailedRetryMaxMs < recognizeFailedRetryMs
                || featureCropScale < 0 || (featureCropScale > 0 && featureCropScale < 1))
            throw new IllegalArgumentException("Invalid session config");
    }
}
