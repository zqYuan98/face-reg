package com.bonuos.facesdk.engine;

/**
 * Local (not sync-protocol) publication barrier shared by all feature spaces of one dataset.
 * Lock order: dataset writer -> this monitor; session -> this monitor. A writer must never call
 * a session while holding this monitor. Only the short authorization commit runs under it;
 * loading models, searching galleries and user callbacks do not.
 */
public final class DatasetEpoch {
    private long generation;
    private boolean ready = true;

    /** Revoke the previous generation before changing any online data. */
    public synchronized long invalidate() { ready = false; return ++generation; }
    public synchronized long generation() { return generation; }
    public synchronized boolean isCurrent(long expected) { return ready && generation == expected; }
    public synchronized void markReady(long expected) {
        if (generation == expected) ready = true;
    }
    /** The action must only commit in-memory state, never invoke user code. */
    public synchronized boolean commit(long expected, Runnable action) {
        if (!isCurrent(expected)) return false;
        action.run();
        return true;
    }
}
