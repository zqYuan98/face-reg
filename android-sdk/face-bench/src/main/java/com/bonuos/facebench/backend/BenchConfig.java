package com.bonuos.facebench.backend;

import java.util.ArrayList;
import java.util.List;

/** Replay settings. Written verbatim to run.json so a report can always be traced to its inputs. */
public final class BenchConfig {
    public List<String> backends = new ArrayList<>();
    /** Liveness runs on every frame: each frame is a liveness observation. */
    public boolean livenessEveryFrame = true;
    /** Extract a recognition feature on every Nth frame (1 = every frame). */
    public int featureEveryNth = 3;
    /** Frames at the start of each clip excluded from latency statistics (model and tracker warm-up). */
    public int warmupFramesPerClip = 2;
    /** ArcSoft liveness thresholds swept per frame; 0.5 (its default) is always included. */
    public float[] arcSoftLivenessThresholds = {0.1f, 0.2f, 0.3f, 0.4f, 0.5f, 0.6f, 0.7f, 0.8f, 0.9f};
    /** Use ArcSoft's ID_CARD comparison model when the references are ID-card chip photos. */
    public boolean arcSoftIdCardModel = false;

    public void validate() {
        if (backends.isEmpty()) throw new IllegalArgumentException("Select at least one backend");
        if (featureEveryNth < 1 || featureEveryNth > 100) throw new IllegalArgumentException("featureEveryNth must be 1..100");
        if (warmupFramesPerClip < 0 || warmupFramesPerClip > 30) throw new IllegalArgumentException("warmup must be 0..30");
        for (float threshold : arcSoftLivenessThresholds)
            if (!(threshold > 0f && threshold < 1f)) throw new IllegalArgumentException("ArcSoft thresholds must be in (0,1)");
    }
}
