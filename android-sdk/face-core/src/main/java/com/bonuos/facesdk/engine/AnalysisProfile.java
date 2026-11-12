package com.bonuos.facesdk.engine;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Immutable presets for paired analysis experiments; no recognition or liveness policy changes. */
public final class AnalysisProfile {
    public static final int VERSION = 1;
    public static final String TIMING_LEVEL = "detector_stages_v1";
    public static final int DETECTOR_LONG_SIDE = 320;
    public static final float DETECTOR_THRESHOLD = .5f;
    public static final int FULL_SCAN_EVERY = 10;
    public static final int REGION_LONG_SIDE = 160;
    public static final float REGION_SCALE = 2f;
    public static final float REGION_MIN_IOU = .2f;
    public static final int DETECTOR_THREADS = 2;
    public static final int FEATURE_THREADS = 4;
    public static final int LIVENESS_THREADS = 2;
    public static final int LANDMARK_THREADS = 1;

    public final String variant;
    public final boolean shareFeatureCapture;
    public final int detectorLongSide = DETECTOR_LONG_SIDE;
    public final float detectorThreshold = DETECTOR_THRESHOLD;
    public final int fullScanEvery = FULL_SCAN_EVERY;
    public final int regionLongSide = REGION_LONG_SIDE;
    public final float regionScale = REGION_SCALE;
    public final float regionMinIou = REGION_MIN_IOU;
    public final int detectorThreads = DETECTOR_THREADS;
    public final int featureThreads = FEATURE_THREADS;
    public final int livenessThreads = LIVENESS_THREADS;
    public final int landmarkThreads = LANDMARK_THREADS;
    public final boolean earlyFrameAdmission = false;
    public final String timingLevel = TIMING_LEVEL;

    private AnalysisProfile(String variant, boolean shareFeatureCapture) {
        this.variant = variant;
        this.shareFeatureCapture = shareFeatureCapture;
    }

    public static AnalysisProfile forVariant(String variant) {
        if ("baseline".equals(variant)) return new AnalysisProfile(variant, false);
        if ("shared_capture".equals(variant)) return new AnalysisProfile(variant, true);
        throw new IllegalArgumentException("Unknown analysis variant: " + variant);
    }

    public void applyTo(SessionConfig config) {
        if (config == null) throw new IllegalArgumentException("SessionConfig is required");
        config.shareFeatureCapture = shareFeatureCapture;
    }

    /** Explicit keys, independent of reflection or obfuscation; values are fingerprint-safe. */
    public Map<String, Object> toMap() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("version", VERSION);
        out.put("variant", variant);
        out.put("shareFeatureCapture", shareFeatureCapture);
        out.put("detectorLongSide", detectorLongSide);
        out.put("detectorThreshold", detectorThreshold);
        out.put("fullScanEvery", fullScanEvery);
        out.put("regionLongSide", regionLongSide);
        out.put("regionScale", regionScale);
        out.put("regionMinIou", regionMinIou);
        out.put("detectorThreads", detectorThreads);
        out.put("featureThreads", featureThreads);
        out.put("livenessThreads", livenessThreads);
        out.put("landmarkThreads", landmarkThreads);
        out.put("earlyFrameAdmission", earlyFrameAdmission);
        out.put("timingLevel", timingLevel);
        return Collections.unmodifiableMap(out);
    }
}
