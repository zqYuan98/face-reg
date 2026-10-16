package com.bonuos.facebench.backend;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;

/** One shared deadline, never a fresh timeout per pool. Does not free any engine handles. */
public final class WorkerDrain {
    private WorkerDrain() { }
    public static boolean finish(ExecutorService first, ExecutorService second, long timeoutMs,
                                 Runnable firstFinished) {
        if (timeoutMs < 0) throw new IllegalArgumentException("Negative drain timeout");
        long budget = TimeUnit.MILLISECONDS.toNanos(timeoutMs), start = System.nanoTime();
        if (first != null) first.shutdown();
        if (second != null) second.shutdown();
        try {
            boolean a = await(first, budget, start);
            firstFinished.run();
            boolean b = await(second, budget, start);
            return a && b;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }
    private static boolean await(ExecutorService pool, long budget, long start) throws InterruptedException {
        return pool == null || pool.awaitTermination(Math.max(0, budget - (System.nanoTime() - start)), TimeUnit.NANOSECONDS);
    }
}
