package com.bonuos.face.service;

import com.bonuos.face.dto.RevisionClock;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.FaceSearchMapper;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.util.FaceUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import java.util.List;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;

/**
 * Exact 1:N search. Cached groups are scanned in memory ({@link FaceGallery}); others are streamed
 * from the database with constant batch memory. A deadline means incomplete, never no match.
 */
@Service
public class FaceSearchService {
    private final FaceSearchMapper mapper;
    private final UserMapper users;
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

    /**
     * {@code threshold} is uncalibrated (0.6 historically); set it from a FAR-calibrated profile.
     * {@code margin} &gt; 0 refuses a best match whose lead over the runner-up is smaller than it.
     * Callers wait for a slot within the search budget instead of failing at once.
     */
    @Autowired
    public FaceSearchService(FaceSearchMapper mapper, UserMapper users, FaceGallery gallery,
            @Value("${face.search.concurrency:4}") int concurrency,
            @Value("${face.search.chunk-size:200}") int chunkSize,
            @Value("${face.search.timeout-ms:5000}") long timeoutMs,
            @Value("${face.search.threshold:0.6}") float threshold,
            @Value("${face.search.margin:0}") float margin) {
        if (concurrency < 1 || concurrency > 64 || chunkSize < 1 || chunkSize > 500 || timeoutMs < 1 || timeoutMs > 60000
                || !(threshold > 0f && threshold < 1f) || !(margin >= 0f && margin < 1f))
            throw new IllegalArgumentException("Invalid search limits");
        this.mapper = mapper; this.users = users; this.gallery = gallery; this.slots = new Semaphore(concurrency);
        this.chunkSize = chunkSize; this.budgetNanos = TimeUnit.MILLISECONDS.toNanos(timeoutMs);
        this.threshold = threshold; this.margin = margin;
    }

    public User search(List<Float> target, long groupId) {
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
            float[] probe = FaceUtils.normalizedCopy(target);
            if (probe == null) throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_FEATURE", "特征数据无效");
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
            List<User> chunk = mapper.nextChunk(groupId, after, chunkSize);
            checkDeadline(deadline);
            for (User row : chunk) {
                checkDeadline(deadline);
                float[] vector = FaceUtils.parseFeatureArray(row.getFeatureData());
                if (vector != null) {
                    float similarity = FaceUtils.cosineToUnit(probe, vector);
                    if (Float.isFinite(similarity) && similarity > score) {
                        if (best != null) second = score;
                        score = similarity; best = row.getId(); bestVersion = row.getSyncVersion();
                    } else if (Float.isFinite(similarity) && similarity > second) {
                        second = similarity;
                    }
                }
                after = row.getId();
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
