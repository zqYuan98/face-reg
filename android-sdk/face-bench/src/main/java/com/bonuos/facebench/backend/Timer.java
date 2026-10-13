package com.bonuos.facebench.backend;

/** Monotonic millisecond timing with sub-millisecond resolution. */
public final class Timer {
    private long started;

    public static Timer start() {
        Timer timer = new Timer();
        timer.started = System.nanoTime();
        return timer;
    }

    public double elapsedMs() {
        return (System.nanoTime() - started) / 1_000_000.0;
    }

    public double lapMs() {
        long now = System.nanoTime();
        double elapsed = (now - started) / 1_000_000.0;
        started = now;
        return elapsed;
    }
}
