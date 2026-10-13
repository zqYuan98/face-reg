package com.bonuos.facebench.backend;

import java.util.LinkedHashMap;
import java.util.Map;

/** Everything one backend reports for one frame. Missing timings stay NaN, never zero. */
public final class FrameOutcome {
    /** Liveness status codes shared by all backends, aligned with ArcSoft's LivenessInfo values. */
    public static final int LIVE_ALIVE = 1, LIVE_NOT_ALIVE = 0, LIVE_UNKNOWN = -1,
            LIVE_MULTIPLE_FACES = -2, LIVE_FACE_TOO_SMALL = -3, LIVE_ANGLE_TOO_LARGE = -4,
            LIVE_BEYOND_BOUNDARY = -5, LIVE_NOT_RUN = -100, LIVE_ERROR = -101;

    public int faceCount;
    /** Main face box in upright frame coordinates: left, top, right, bottom; null when no face. */
    public int[] box;
    public double preprocessMs = Double.NaN;
    public double detectMs = Double.NaN;
    public double livenessMs = Double.NaN;
    public double featureMs = Double.NaN;
    /** Continuous liveness score where the backend exposes one, otherwise NaN. */
    public double livenessScore = Double.NaN;
    /** Status at the backend's default threshold. */
    public int livenessStatus = LIVE_NOT_RUN;
    /** Status per threshold for backends that only expose decisions (threshold sweep). */
    public final Map<Float, Integer> livenessStatusByThreshold = new LinkedHashMap<>();
    public Object feature;
    public boolean featureAttempted;
    public String note = "";
}
