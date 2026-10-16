package com.bonuos.facebench.backend;

/** Immutable worker observation. No feature vectors, image data or mutable engine objects. */
public final class BackendEvent {
    public static final int SCHEMA_VERSION = 1;
    public final long epoch, sequence, trackId, frameId;
    public final String kind, actualIdentity, error;
    /** Monotonic wall milliseconds since the first replay frame; never clipped to clip duration. */
    public final long elapsedMs;
    public final double score;
    public final int livenessStatus;

    BackendEvent(long epoch, long sequence, long trackId, long frameId, String kind,
                 String actualIdentity, long elapsedMs, double score, int livenessStatus, String error) {
        this.epoch = epoch;
        this.sequence = sequence;
        this.trackId = trackId;
        this.frameId = frameId;
        this.kind = kind;
        this.actualIdentity = actualIdentity == null ? "" : actualIdentity;
        this.elapsedMs = elapsedMs;
        this.score = score;
        this.livenessStatus = livenessStatus;
        this.error = error == null ? "" : error;
    }
}
