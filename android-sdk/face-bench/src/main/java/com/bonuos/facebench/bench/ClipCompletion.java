package com.bonuos.facebench.bench;

/** A clip is accounted for even when opening, processing, cancellation or drain fails. */
public final class ClipCompletion {
    public final String backend, clipId;
    public long epoch;
    public String stopReason = "error", error = "", drainError = "";
    public boolean eof, cancelled, drainOk;
    public int receivedFrames, frameErrors, timestampErrors, droppedFrames;
    public long firstTimestampNs = -1, lastTimestampNs = -1, eventCount, eventErrors, eventOverflow;
    public long inWindowEvents, tailEvents;
    public double drainElapsedMs;
    public Object effectiveConfig, metrics;
    public String effectiveConfigSha256;
    public ClipCompletion(String backend, String clipId) { this.backend = backend; this.clipId = clipId; }
    public boolean complete() {
        return eof && !cancelled && error.isEmpty() && drainOk && frameErrors == 0
                && timestampErrors == 0 && eventOverflow == 0 && eventErrors == 0;
    }
}
