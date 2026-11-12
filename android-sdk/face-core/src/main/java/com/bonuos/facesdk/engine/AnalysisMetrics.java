package com.bonuos.facesdk.engine;

/** One detector call, on the analysis thread. Durations are nested wall time, not CPU time. */
public final class AnalysisMetrics {
    public static final AnalysisMetrics UNAVAILABLE = new AnalysisMetrics("NONE", "UNAVAILABLE",
            0, 0, -1, -1, -1, -1, -1, -1, -1, 0, 0);

    public final String mode, status;
    public final int fullCalls, roiCalls;
    /** -1 means the stage did not run or was not measured; zero is a measured duration. */
    public final long copyNanos, sampleNanos, preprocessNanos, inferenceNanos, postprocessNanos;
    public final long fullNanos, roiNanos;
    public final long copyBytes, copyReallocations;

    public AnalysisMetrics(String mode, String status, int fullCalls, int roiCalls,
                           long copyNanos, long sampleNanos, long preprocessNanos,
                           long inferenceNanos, long postprocessNanos, long fullNanos, long roiNanos,
                           long copyBytes, long copyReallocations) {
        this.mode = mode;
        this.status = status;
        this.fullCalls = fullCalls;
        this.roiCalls = roiCalls;
        this.copyNanos = copyNanos;
        this.sampleNanos = sampleNanos;
        this.preprocessNanos = preprocessNanos;
        this.inferenceNanos = inferenceNanos;
        this.postprocessNanos = postprocessNanos;
        this.fullNanos = fullNanos;
        this.roiNanos = roiNanos;
        this.copyBytes = copyBytes;
        this.copyReallocations = copyReallocations;
    }
}
