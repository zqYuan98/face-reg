package com.bonuos.facebench.backend;

import static org.junit.Assert.*;
import java.util.concurrent.*;
import org.junit.Test;

public class WorkerDrainTest {
    @Test public void drainsBothWorkersAndWakesNoMoreLivenessWaiter() throws Exception {
        ExecutorService fl = Executors.newSingleThreadExecutor(), fr = Executors.newSingleThreadExecutor();
        CountDownLatch done = new CountDownLatch(1);
        fr.execute(() -> { try { done.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); } });
        assertTrue(WorkerDrain.finish(fl, fr, 1000, done::countDown));
        assertTrue(fl.isTerminated()); assertTrue(fr.isTerminated());
    }

    @Test public void ignoresNativeInterruptMeansTimeoutNotSafeToFree() throws Exception {
        ExecutorService fl = Executors.newSingleThreadExecutor(), fr = Executors.newSingleThreadExecutor();
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        fl.execute(() -> {
            entered.countDown();
            while (release.getCount() > 0) {
                try { release.await(); } catch (InterruptedException ignored) { }
            }
        });
        assertTrue(entered.await(1, TimeUnit.SECONDS));
        try {
            assertFalse(WorkerDrain.finish(fl, fr, 0, () -> { }));
            assertFalse(fl.isTerminated());
            assertTrue(fl.isShutdown());
            assertThrows(RejectedExecutionException.class, () -> fl.execute(() -> { }));
        } finally {
            release.countDown();
            assertTrue(fl.awaitTermination(1, TimeUnit.SECONDS));
            fr.shutdownNow();
        }
    }
}
