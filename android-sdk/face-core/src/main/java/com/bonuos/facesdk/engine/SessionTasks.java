package com.bonuos.facesdk.engine;

import java.util.concurrent.TimeUnit;

/** Counts accepted frames and task chains, including completion callbacks; owns no engine. */
final class SessionTasks {
    private int pending;
    private boolean stopped, revoked;
    private final ThreadLocal<Integer> depth = ThreadLocal.withInitial(() -> 0);

    synchronized boolean reserveFrame() {
        if (stopped) return false;
        pending++;
        return true;
    }
    synchronized void stopInput() { stopped = true; }
    synchronized void revoke() { stopped = revoked = true; }
    synchronized void authorize(Runnable commit) { if (!revoked) commit.run(); }
    synchronized void reserve() { pending++; }
    void enter() { depth.set(depth.get() + 1); }
    void leave() { int n = depth.get() - 1; if (n == 0) depth.remove(); else depth.set(n); }
    synchronized void complete() { pending--; notifyAll(); }
    boolean isParticipant() { return depth.get() != 0; }

    boolean await(long timeoutMs) {
        if (isParticipant()) return false; // never wait for our own task/callback
        long budget = TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        long start = System.nanoTime();
        synchronized (this) {
            while (pending != 0) {
                long remaining = budget - (System.nanoTime() - start);
                if (remaining <= 0) return false;
                try { TimeUnit.NANOSECONDS.timedWait(this, remaining); }
                catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return false;
                }
            }
            return true;
        }
    }
}
