package com.bonuos.facebench.backend;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/** Bounded multi-producer event journal. Workers enqueue only; the replay thread does all I/O. */
public final class BackendEvents {
    private final int capacity;
    private final ArrayDeque<BackendEvent> events = new ArrayDeque<>();
    private final ArrayDeque<BackendEvent> legacyRecognitions = new ArrayDeque<>();
    private long epoch, sequence, overflow;
    public BackendEvents(int capacity) {
        if (capacity < 1) throw new IllegalArgumentException("Event capacity must be positive");
        this.capacity = capacity;
    }
    /** Only called once the previous clip's workers have terminated. */
    public synchronized long begin() {
        if (!events.isEmpty()) throw new IllegalStateException("Undrained clip events");
        legacyRecognitions.clear();
        sequence = overflow = 0;
        return ++epoch;
    }
    public synchronized void add(long capturedEpoch, long trackId, long frameId, String kind,
                                 String identity, long elapsedMs, double score, int live, String error) {
        if (capturedEpoch != epoch) return; // An old callback can never be relabelled as a new clip.
        long seq = ++sequence;
        if (events.size() == capacity) { overflow++; return; }
        BackendEvent event = new BackendEvent(epoch, seq, trackId, frameId, kind, identity, elapsedMs, score, live, error);
        events.addLast(event);
        if ("recognized".equals(kind)) {
            if (legacyRecognitions.size() == capacity) legacyRecognitions.removeFirst();
            legacyRecognitions.addLast(event);
        }
    }
    public synchronized List<BackendEvent> drain() {
        List<BackendEvent> result = new ArrayList<>(events);
        events.clear();
        return result;
    }
    public synchronized long overflow() { return overflow; }
    public synchronized long epoch() { return epoch; }
    /** Best-effort legacy frames.csv note; events.csv is the authoritative complete journal. */
    public synchronized BackendEvent recognitionAfter(long after) {
        while (!legacyRecognitions.isEmpty()) {
            BackendEvent event = legacyRecognitions.removeFirst();
            if (event.sequence > after) return event;
        }
        return null;
    }
}
