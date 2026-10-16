package com.bonuos.facesdk;

import com.bonuos.facesdk.engine.DatasetEpoch;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.locks.ReentrantReadWriteLock;

/** Serializes dataset publication and recognition within this process. */
final class FaceDatasetCoordinator {
    static final ReentrantReadWriteLock LOCK = new ReentrantReadWriteLock(true);
    private static final CopyOnWriteArrayList<Runnable> listeners = new CopyOnWriteArrayList<>();
    static final DatasetEpoch EPOCH = new DatasetEpoch();
    private static boolean refreshRequired;
    private FaceDatasetCoordinator() { }
    static void add(Runnable listener) { listeners.add(listener); }
    static void remove(Runnable listener) { listeners.remove(listener); }
    static void publish(Runnable commit) {
        LOCK.writeLock().lock();
        try {
            EPOCH.invalidate(); // revoke even if commit or any refresh subsequently fails
            refreshRequired = true;
            commit.run();
            refreshIfRequired();
        } finally { LOCK.writeLock().unlock(); }
    }
    static void refreshIfRequired() {
        LOCK.writeLock().lock();
        try {
            if (!refreshRequired) return;
            RuntimeException failure = null;
            for (Runnable listener : listeners) {
                try { listener.run(); } catch (RuntimeException error) { failure = error; }
            }
            refreshRequired = failure != null;
            if (failure != null) throw failure;
            EPOCH.markReady(EPOCH.generation());
        } finally { LOCK.writeLock().unlock(); }
    }
}
