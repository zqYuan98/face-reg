package com.bonuos.facebench.backend;

import static org.junit.Assert.*;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import org.junit.Test;

public class BackendEventsTest {
    @Test public void retainsMultipleImmutableEventsAndRejectsOldEpoch() {
        BackendEvents queue = new BackendEvents(4);
        long first = queue.begin();
        queue.add(first, 7, 4, "recognized", "alice", 80, .9, 1, "");
        queue.add(first, 8, 5, "recognized", "bob", 90, .8, 1, "");
        List<BackendEvent> events = queue.drain();
        assertEquals(2, events.size());
        assertEquals("alice", events.get(0).actualIdentity);
        assertEquals(2, events.get(1).sequence);
        // Independent legacy consumption cannot lose a callback drained between frames.
        assertEquals("alice", queue.recognitionAfter(0).actualIdentity);
        assertEquals("bob", queue.recognitionAfter(1).actualIdentity);
        long next = queue.begin();
        queue.add(first, 7, 4, "recognized", "stale", 100, .9, 1, "");
        queue.add(next, 7, 0, "liveness", "", 0, .8, 1, "");
        assertEquals(1, queue.drain().size());
        assertEquals("alice", events.get(0).actualIdentity);
    }

    @Test public void overflowIsBoundedAndNotSilent() {
        BackendEvents queue = new BackendEvents(1);
        long epoch = queue.begin();
        queue.add(epoch, 1, 0, "recognized", "a", 1, 1, 1, "");
        queue.add(epoch, 2, 0, "recognized", "b", 2, 1, 1, "");
        assertEquals(1, queue.overflow());
        assertThrows(IllegalStateException.class, queue::begin);
        assertEquals(1, queue.drain().get(0).sequence);
        queue.add(epoch, 3, 0, "recognized", "c", 3, 1, 1, "");
        assertEquals(3, queue.drain().get(0).sequence);
    }

    @Test public void concurrentProducersHaveUniqueContiguousSequences() throws Exception {
        BackendEvents queue = new BackendEvents(200);
        long epoch = queue.begin();
        CountDownLatch go = new CountDownLatch(1);
        Runnable producer = () -> {
            try { go.await(); } catch (InterruptedException e) { throw new AssertionError(e); }
            for (int i = 0; i < 100; i++) queue.add(epoch, i, i, "liveness", "", i, .5, -1, "");
        };
        Thread a = new Thread(producer), b = new Thread(producer);
        a.start(); b.start(); go.countDown(); a.join(); b.join();
        List<BackendEvent> events = queue.drain();
        assertEquals(200, events.size());
        for (int i = 0; i < events.size(); i++) assertEquals(i + 1, events.get(i).sequence);
    }
}
