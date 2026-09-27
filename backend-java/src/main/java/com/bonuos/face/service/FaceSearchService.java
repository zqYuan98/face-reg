package com.bonuos.face.service;

import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.FaceSearchMapper;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.util.FaceUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import java.util.List;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;

/** Exact search with constant batch memory. A deadline means incomplete, never no match. */
@Service
public class FaceSearchService {
    private final FaceSearchMapper mapper;
    private final UserMapper users;
    private final Semaphore slots;
    private final int chunkSize;
    private final long budgetNanos;

    public FaceSearchService(FaceSearchMapper mapper, UserMapper users,
            @Value("${face.search.concurrency:1}") int concurrency,
            @Value("${face.search.chunk-size:200}") int chunkSize,
            @Value("${face.search.timeout-ms:5000}") long timeoutMs) {
        if (concurrency < 1 || concurrency > 4 || chunkSize < 1 || chunkSize > 500 || timeoutMs < 1 || timeoutMs > 60000)
            throw new IllegalArgumentException("Invalid search limits");
        this.mapper = mapper; this.users = users; this.slots = new Semaphore(concurrency);
        this.chunkSize = chunkSize; this.budgetNanos = TimeUnit.MILLISECONDS.toNanos(timeoutMs);
    }

    public User search(List<Float> target, long groupId) {
        if (groupId <= 0) throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_GROUP", "请选择有效分组");
        if (!slots.tryAcquire()) throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "SEARCH_BUSY", "检索繁忙，请稍后重试");
        long deadline = System.nanoTime() + budgetNanos;
        try {
            String startingRevision = mapper.revision();
            long after = 0; Long best = null; Long bestVersion = null; float score = 0;
            while (true) {
                checkDeadline(deadline);
                List<User> chunk = mapper.nextChunk(groupId, after, chunkSize);
                checkDeadline(deadline);
                for (User row : chunk) {
                    checkDeadline(deadline);
                    List<Float> vector = FaceUtils.parseFeatureData(row.getFeatureData());
                    if (vector != null) {
                        float similarity = FaceUtils.cosineSimilarity(target, vector);
                        if (Float.isFinite(similarity) && similarity > score) { score = similarity; best = row.getId(); bestVersion = row.getSyncVersion(); }
                    }
                    after = row.getId();
                }
                if (chunk.size() < chunkSize) break;
            }
            if (best == null || score < 0.6f) {
                verifyRevision(startingRevision,deadline);
                return null;
            }
            // A concurrent mutation may invalidate the winning row; don't return a different group.
            User result = users.selectById(best);
            verifyRevision(startingRevision,deadline);
            checkDeadline(deadline);
            if (result == null || result.getStatus() != 1 || !Long.valueOf(groupId).equals(result.getGroupId()) || !java.util.Objects.equals(bestVersion,result.getSyncVersion()))
                throw new ApiException(HttpStatus.CONFLICT, "SEARCH_CHANGED", "检索期间人员发生变化，请重试");
            return result;
        } finally { slots.release(); }
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
