package com.bonuos.face.service;

import com.bonuos.face.dto.FeatureRow;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.UserFeatureMapper;
import com.bonuos.face.util.FaceFeatureExtractorClient;
import com.bonuos.face.util.FaceUtils;
import com.bonuos.face.util.FeatureVersions;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Extracts a feature version that people enrolled before it existed do not have, from their stored
 * photos. One run makes a single pass in id order; people that fail stay missing and a later run
 * retries them, so an interrupted run resumes where the data says, not where memory says. A feature
 * is written only if the person still has the photo it came from, so a re-enrolment during the run
 * always wins.
 */
@Service
public class FeatureBackfillService {
    private static final Logger log = LoggerFactory.getLogger(FeatureBackfillService.class);
    private static final int PAGE = 100, RECENT_FAILURES = 50;

    private final UserFeatureMapper features;
    private final UserFeatureStore store;
    private final PhotoStorage photos;
    private final FaceFeatureExtractorClient extractor;
    private final FaceSearchService search;
    private final int concurrency;
    private final AtomicBoolean running = new AtomicBoolean(), stopRequested = new AtomicBoolean();
    private final AtomicLong processed = new AtomicLong(), written = new AtomicLong(), failed = new AtomicLong();
    private final Deque<Failure> recentFailures = new ArrayDeque<>();
    private volatile String version;
    private volatile ExecutorService pool;

    public record Failure(long userId, String reason) {}
    public record Status(String version, boolean running, long enrollable, long current, long processed, long written,
                         long failed, List<Failure> recentFailures) {}

    public FeatureBackfillService(UserFeatureMapper features, UserFeatureStore store, PhotoStorage photos, FaceFeatureExtractorClient extractor,
            FaceSearchService search, @Value("${face.features.backfill-concurrency:2}") int concurrency) {
        if (concurrency < 1 || concurrency > 16) throw new IllegalArgumentException("Invalid backfill concurrency");
        this.features = features; this.store = store; this.photos = photos; this.extractor = extractor; this.search = search;
        this.concurrency = concurrency;
    }

    /** Starts a run for {@code version}; false when a run is already going. */
    public boolean start(String version) {
        if (FeatureVersions.V1.equals(FeatureVersions.require(version)))
            throw new ApiException(HttpStatus.BAD_REQUEST, "UNSUPPORTED_FEATURE_VERSION", "v1 特征随人员记录保存，无需补提");
        if (!running.compareAndSet(false, true)) return false;
        this.version = version;
        stopRequested.set(false); processed.set(0); written.set(0); failed.set(0);
        synchronized (recentFailures) { recentFailures.clear(); }
        pool = Executors.newFixedThreadPool(concurrency + 1, r -> {
            Thread t = new Thread(r, "feature-backfill"); t.setDaemon(true); return t;
        });
        pool.execute(() -> {
            try { run(version); }
            catch (RuntimeException e) { log.warn("Feature backfill {} stopped: {}", version, e.getClass().getSimpleName()); }
            finally {
                running.set(false);
                pool.shutdown();
                log.info("Feature backfill {} finished: processed {}, written {}, failed {}", version, processed.get(), written.get(), failed.get());
            }
        });
        return true;
    }

    public void stop() { stopRequested.set(true); }

    @PreDestroy public void shutdown() {
        stop();
        ExecutorService current = pool;
        if (current != null) current.shutdownNow();
    }

    public Status status(String version) {
        FeatureVersions.require(version);
        boolean active = running.get() && version.equals(this.version);
        List<Failure> failures;
        synchronized (recentFailures) { failures = version.equals(this.version) ? List.copyOf(recentFailures) : List.of(); }
        boolean same = version.equals(this.version);
        return new Status(version, active, features.countEnrollable(),
                FeatureVersions.V1.equals(version) ? -1 : features.countCurrent(version),
                same ? processed.get() : 0, same ? written.get() : 0, same ? failed.get() : 0, failures);
    }

    private void run(String version) {
        int dimension = FeatureVersions.dimension(version);
        long after = 0;
        while (!stopRequested.get()) {
            List<FeatureRow> page = features.missing(version, after, PAGE);
            if (page.isEmpty()) return;
            List<Future<Long>> pending = new ArrayList<>();
            for (FeatureRow row : page) pending.add(pool.submit(() -> extractOne(version, dimension, row)));
            Set<Long> groups = new HashSet<>();
            for (Future<Long> result : pending) {
                try {
                    Long group = result.get();
                    if (group != null) groups.add(group);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                } catch (Exception e) {
                    // extractOne records its own failures
                }
            }
            // Backfilled rows bypass the change log, so cached groups must be rebuilt to see them.
            for (Long group : groups) search.featuresChanged(version, group);
            after = page.get(page.size() - 1).getId();
        }
    }

    /** The person's group when a feature was written, otherwise null. */
    private Long extractOne(String version, int dimension, FeatureRow row) {
        if (stopRequested.get()) return null;
        processed.incrementAndGet();
        try {
            Path photo = photos.published(row.getPhotoUrl());
            if (photo == null) { fail(row.getId(), "PHOTO_MISSING"); return null; }
            float[] feature = extractor.extractFeature(photo.toFile(), version);
            if (feature.length != dimension || !FaceUtils.normalizeInPlace(feature)) { fail(row.getId(), "INVALID_FEATURE"); return null; }
            if (store.putIfPhotoCurrent(row.getId(), version, dimension, FaceUtils.toBlob(feature), row.getPhotoUrl()) == 0)
                return null; // re-enrolled or removed meanwhile; the newer state wins
            written.incrementAndGet();
            return row.getGroupId();
        } catch (ApiException e) {
            fail(row.getId(), e.getErrorCode());
            if ("ALGORITHM_UNAVAILABLE".equals(e.getErrorCode()) || "ALGORITHM_TIMEOUT".equals(e.getErrorCode())) sleepQuietly();
            return null;
        } catch (Exception e) {
            fail(row.getId(), e.getClass().getSimpleName());
            return null;
        }
    }

    private void fail(long userId, String reason) {
        failed.incrementAndGet();
        synchronized (recentFailures) {
            if (recentFailures.size() == RECENT_FAILURES) recentFailures.removeFirst();
            recentFailures.addLast(new Failure(userId, reason));
        }
    }

    private static void sleepQuietly() {
        try { Thread.sleep(1000); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }
}
