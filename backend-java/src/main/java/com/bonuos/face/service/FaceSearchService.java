package com.bonuos.face.service;

import com.bonuos.face.dto.RevisionClock;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.FaceSearchMapper;
import com.bonuos.face.mapper.UserFeatureMapper;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.util.FaceUtils;
import com.bonuos.face.util.FeatureVersions;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import java.util.List;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;

/**
 * Exact 1:N search over one feature version ({@code face.search.feature-version}). Cached groups
 * are scanned in memory ({@link FaceGallery}); others are streamed from the database with constant
 * batch memory. A deadline means incomplete, never no match.
 */
@Service
public class FaceSearchService {
    private final FaceSearchMapper mapper;
    private final UserMapper users;
    private final FeatureSource source;
    private final FaceGallery gallery;
    private final Semaphore slots;
    private final int chunkSize;
    private final long budgetNanos;
    private final float threshold;
    private final float margin;

    public FaceSearchService(FaceSearchMapper mapper, UserMapper users, int concurrency, int chunkSize, long timeoutMs) {
        this(mapper, users, concurrency, chunkSize, timeoutMs, 0.6f);
    }

    public FaceSearchService(FaceSearchMapper mapper, UserMapper users, int concurrency, int chunkSize, long timeoutMs, float threshold) {
        this(mapper, users, null, concurrency, chunkSize, timeoutMs, threshold, 0f);
    }

    /** v1 search over a given (possibly absent) gallery. */
    public FaceSearchService(FaceSearchMapper mapper, UserMapper users, FaceGallery gallery,
            int concurrency, int chunkSize, long timeoutMs, float threshold, float margin) {
        this(mapper, users, FeatureSource.v1(mapper), gallery, concurrency, chunkSize, timeoutMs, threshold, margin);
    }

    /**
     * Thresholds are uncalibrated defaults: v1 0.6 (historical), arcface_r50_512 0.45 (LFW: FAR 1e-5
     * at 0.34, 1e-6 at 0.63). Set them from FAR-calibrated data. {@code margin} &gt; 0 refuses a best
     * match whose lead over the runner-up is smaller. Callers wait for a slot within the budget.
     */
    @Autowired
    public FaceSearchService(FaceSearchMapper mapper, UserMapper users, UserFeatureMapper features,
            @Value("${face.search.feature-version:recognizer_v1_1024}") String featureVersion,
            @Value("${face.search.concurrency:4}") int concurrency,
            @Value("${face.search.chunk-size:200}") int chunkSize,
            @Value("${face.search.timeout-ms:5000}") long timeoutMs,
            @Value("${face.search.threshold:0.6}") float threshold,
            @Value("${face.search.v2-threshold:0.45}") float v2Threshold,
            @Value("${face.search.margin:0}") float margin,
            @Value("${face.search.gallery.max-bytes:134217728}") long galleryBytes,
            @Value("${face.search.gallery.max-delta:2000}") int galleryDelta) {
        this(mapper, users, FeatureSource.of(featureVersion, mapper, features),
                galleryBytes > 0 ? new FaceGallery(mapper, FeatureSource.of(featureVersion, mapper, features), galleryBytes, galleryDelta) : null,
                concurrency, chunkSize, timeoutMs, FeatureVersions.V1.equals(featureVersion) ? threshold : v2Threshold, margin);
    }

    FaceSearchService(FaceSearchMapper mapper, UserMapper users, FeatureSource source, FaceGallery gallery,
            int concurrency, int chunkSize, long timeoutMs, float threshold, float margin) {
        if (concurrency < 1 || concurrency > 64 || chunkSize < 1 || chunkSize > 500 || timeoutMs < 1 || timeoutMs > 60000
                || !(threshold > 0f && threshold < 1f) || !(margin >= 0f && margin < 1f))
            throw new IllegalArgumentException("Invalid search limits");
        this.mapper = mapper; this.users = users; this.source = source; this.gallery = gallery;
        this.slots = new Semaphore(concurrency);
        this.chunkSize = chunkSize; this.budgetNanos = TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        this.threshold = threshold; this.margin = margin;
    }

    @PreDestroy public void shutdown() { if (gallery != null) gallery.shutdown(); }

    /** The feature version probes must be extracted with. */
    public String featureVersion() { return source.version(); }

    /** A feature of the search version changed outside the change log (backfill); drop the cached group. */
    public void featuresChanged(String version, long groupId) {
        if (gallery != null && source.version().equals(version)) gallery.invalidate(groupId);
    }

    public User search(List<Float> target, long groupId) {
        float[] probe = new float[target == null ? 0 : target.size()];
        for (int i = 0; i < probe.length; i++) {
            Float value = target.get(i);
            probe[i] = value == null ? Float.NaN : value;
        }
        return search(probe, groupId);
    }

    public User search(float[] target, long groupId) {
        if (groupId <= 0) throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_GROUP", "请选择有效分组");
        long deadline = System.nanoTime() + budgetNanos;
        try {
            if (!slots.tryAcquire(budgetNanos, TimeUnit.NANOSECONDS))
                throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "SEARCH_BUSY", "检索繁忙，请稍后重试");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "SEARCH_INCOMPLETE", "检索被中断，请稍后重试");
        }
        try {
            // Normalise the probe once; each candidate then costs one dot product.
            float[] probe = target.clone();
            if (!FaceUtils.normalizeInPlace(probe)) throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_FEATURE", "特征数据无效");
            String startingRevision = null;
            FaceGallery.Top2 top = null;
            if (gallery != null) {
                RevisionClock clock = mapper.clock();
                if (clock != null) {
                    startingRevision = clock.token();
                    top = gallery.search(groupId, clock, probe);
                }
            }
            if (top == null) {
                startingRevision = mapper.revision();
                top = scanDatabase(groupId, probe, deadline);
            }
            checkDeadline(deadline);
            if (top.bestId() == null || top.best() < threshold) {
                verifyRevision(startingRevision,deadline);
                return null;
            }
            if (margin > 0f && top.best() - top.second() < margin) {
                verifyRevision(startingRevision,deadline);
                throw new ApiException(HttpStatus.NOT_FOUND, "AMBIGUOUS_MATCH", "存在多个相似人员，无法确定身份");
            }
            // A concurrent mutation may invalidate the winning row; don't return a different group.
            User result = users.selectById(top.bestId());
            verifyRevision(startingRevision,deadline);
            checkDeadline(deadline);
            if (result == null || result.getStatus() != 1 || !Long.valueOf(groupId).equals(result.getGroupId()) || !java.util.Objects.equals(top.bestVersion(),result.getSyncVersion()))
                throw new ApiException(HttpStatus.CONFLICT, "SEARCH_CHANGED", "检索期间人员发生变化，请重试");
            return result;
        } finally { slots.release(); }
    }

    private FaceGallery.Top2 scanDatabase(long groupId, float[] probe, long deadline) {
        long after = 0; Long best = null; Long bestVersion = null; float score = 0, second = 0;
        while (true) {
            checkDeadline(deadline);
            List<FeatureSource.Row> chunk = source.nextChunk(groupId, after, chunkSize);
            checkDeadline(deadline);
            for (FeatureSource.Row row : chunk) {
                checkDeadline(deadline);
                if (row.feature() != null) {
                    float similarity = FaceUtils.cosineToUnit(probe, row.feature());
                    if (Float.isFinite(similarity) && similarity > score) {
                        if (best != null) second = score;
                        score = similarity; best = row.id(); bestVersion = row.syncVersion();
                    } else if (Float.isFinite(similarity) && similarity > second) {
                        second = similarity;
                    }
                }
                after = row.id();
            }
            if (chunk.size() < chunkSize) break;
        }
        return new FaceGallery.Top2(best, bestVersion, score, second);
    }

    private void verifyRevision(String startingRevision,long deadline) {
        if (!java.util.Objects.equals(startingRevision,mapper.revision()))
            throw new ApiException(HttpStatus.CONFLICT,"SEARCH_CHANGED","检索期间人员发生变化，请重试");
        checkDeadline(deadline);
    }

    private static void checkDeadline(long deadline) {
        if (Thread.currentThread().isInterrupted() || System.nanoTime() >= deadline)
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "SEARCH_INCOMPLETE", "检索超时，尚未完成全部比对，请缩小分组或稍后重试");
    }
}
