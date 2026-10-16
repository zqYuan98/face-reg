package com.bonuos.face.service;

import com.bonuos.face.dto.RevisionClock;
import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.FaceSearchMapper;
import com.bonuos.face.util.FaceUtils;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.ReentrantReadWriteLock;

/**
 * Per-group, in-memory matrices of unit-length features, so a 1:N search is one pass over a flat
 * float array instead of re-reading and re-parsing every row from MySQL.
 *
 * <p>Each group is tagged with the sync revision it reflects. A search at a newer revision first
 * replays the user_change_log entries that touched the group by re-reading those users' current
 * rows; replays are idempotent, so tagging with the head read before any row is safe. A missing
 * group, a new epoch, a trimmed log or a large backlog triggers a background rebuild, and the
 * caller falls back to the streaming scan until it finishes. Groups that do not fit the memory
 * budget are never cached.
 */
@Component
public class FaceGallery {
    private static final Logger log = LoggerFactory.getLogger(FaceGallery.class);
    private static final int PAGE = 500;
    private static final long TOO_LARGE_RETRY_NANOS = 10L * 60 * 1_000_000_000L;

    private final FaceSearchMapper mapper;
    private final long maxBytes;
    private final int maxDelta;
    private final ExecutorService builder;
    private final Map<Long, Group> groups = new ConcurrentHashMap<>();
    private final Set<Long> building = ConcurrentHashMap.newKeySet();
    private final Map<Long, Long> tooLarge = new ConcurrentHashMap<>();
    private final AtomicLong usedBytes = new AtomicLong();

    @Autowired
    public FaceGallery(FaceSearchMapper mapper,
            @Value("${face.search.gallery.max-bytes:134217728}") long maxBytes,
            @Value("${face.search.gallery.max-delta:2000}") int maxDelta) {
        this(mapper, maxBytes, maxDelta, Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "face-gallery-builder"); t.setDaemon(true); return t;
        }));
    }

    /** {@code builder} runs rebuilds; tests pass a direct executor. */
    FaceGallery(FaceSearchMapper mapper, long maxBytes, int maxDelta, ExecutorService builder) {
        if (maxBytes < 0 || maxDelta < 1) throw new IllegalArgumentException("Invalid gallery limits");
        this.mapper = mapper; this.maxBytes = maxBytes; this.maxDelta = maxDelta; this.builder = builder;
    }

    @PreDestroy public void shutdown() { builder.shutdownNow(); }

    /** Best and runner-up over a group. {@code bestId} is null when nothing scored above zero. */
    public record Top2(Long bestId, Long bestVersion, float best, float second) {}

    /**
     * Top-2 for a unit-length probe at {@code clock}, or null when the group is not cached yet
     * (a rebuild is scheduled) and the caller must scan the database itself.
     */
    public Top2 search(long groupId, RevisionClock clock, float[] probe) {
        if (maxBytes == 0) return null;
        Group group = groups.get(groupId);
        if (group == null) { schedule(groupId); return null; }
        ReentrantReadWriteLock.ReadLock read = group.lock.readLock();
        if (!group.covers(clock)) {
            ReentrantReadWriteLock.WriteLock write = group.lock.writeLock();
            write.lock();
            try {
                if (!group.covers(clock) && !catchUp(groupId, group, clock)) {
                    drop(groupId, group);
                    schedule(groupId);
                    return null;
                }
                read.lock(); // downgrade: no writer can slip in before the scan
            } finally { write.unlock(); }
        } else {
            read.lock();
        }
        try {
            if (!group.covers(clock)) return null;
            group.lastUsed = System.nanoTime();
            return group.scan(probe);
        } finally { read.unlock(); }
    }

    private boolean catchUp(long groupId, Group group, RevisionClock clock) {
        if (!group.epoch.equals(clock.getEpoch()) || group.revision < clock.getRetentionFloor()
                || clock.getHead() - group.revision > maxDelta) return false;
        List<Long> changed = mapper.changedUsers(groupId, group.revision, clock.getHead(), maxDelta + 1);
        if (changed.size() > maxDelta) return false;
        for (int from = 0; from < changed.size(); from += PAGE) {
            List<Long> ids = changed.subList(from, Math.min(changed.size(), from + PAGE));
            Map<Long, User> rows = new HashMap<>();
            for (User row : mapper.rowsByIds(ids)) rows.put(row.getId(), row);
            for (Long id : ids) {
                User row = rows.get(id);
                float[] vector = row != null && Objects.equals(row.getStatus(), 1) && Long.valueOf(groupId).equals(row.getGroupId())
                        ? unitFeature(row) : null;
                if (vector == null) group.remove(id);
                else if (!group.put(id, row.getSyncVersion(), vector)) return false;
            }
        }
        group.revision = clock.getHead();
        return true;
    }

    private void schedule(long groupId) {
        Long since = tooLarge.get(groupId);
        if (since != null && System.nanoTime() - since < TOO_LARGE_RETRY_NANOS) return;
        if (!building.add(groupId)) return;
        try {
            builder.execute(() -> {
                try { build(groupId); }
                catch (RuntimeException e) { log.warn("Face gallery build for group {} failed", groupId, e); }
                finally { building.remove(groupId); }
            });
        } catch (RuntimeException e) {
            building.remove(groupId);
        }
    }

    void build(long groupId) {
        RevisionClock clock = mapper.clock();
        if (clock == null) return;
        Group group = new Group(clock.getEpoch(), clock.getHead());
        long after = 0;
        boolean sized = false;
        while (true) {
            List<User> chunk = mapper.nextChunk(groupId, after, PAGE);
            for (User row : chunk) {
                float[] vector = unitFeature(row);
                if (vector != null && !sized) {
                    // Size the group once from its row count, so a group that cannot fit is refused
                    // before it evicts anything.
                    sized = true;
                    long count = mapper.countActive(groupId);
                    group.dim = vector.length;
                    if (!group.ensureCapacity(count + count / 8 + 64)) { refuse(groupId, group); return; }
                }
                if (vector != null && !group.put(row.getId(), row.getSyncVersion(), vector)) { refuse(groupId, group); return; }
                after = row.getId();
            }
            if (chunk.size() < PAGE) break;
        }
        tooLarge.remove(groupId);
        Group previous = groups.put(groupId, group);
        if (previous != null) release(previous);
        log.info("Face gallery for group {} built at revision {} with {} faces", groupId, clock.getHead(), group.size);
    }

    private void refuse(long groupId, Group group) {
        release(group);
        tooLarge.put(groupId, System.nanoTime());
        log.info("Face group {} exceeds the gallery budget of {} bytes; searching it from the database", groupId, maxBytes);
    }

    private void drop(long groupId, Group group) {
        if (groups.remove(groupId, group)) release(group);
    }

    private void release(Group group) {
        usedBytes.addAndGet(-group.bytes);
        group.bytes = 0;
    }

    /** Reserves {@code bytes}, evicting the least recently used other groups if needed. */
    private boolean reserve(Group owner, long bytes) {
        if (owner.bytes + bytes > maxBytes) return false;
        while (usedBytes.get() + bytes > maxBytes) {
            Map.Entry<Long, Group> victim = null;
            for (Map.Entry<Long, Group> e : groups.entrySet())
                if (e.getValue() != owner && (victim == null || e.getValue().lastUsed < victim.getValue().lastUsed)) victim = e;
            if (victim == null) return false;
            drop(victim.getKey(), victim.getValue());
        }
        usedBytes.addAndGet(bytes);
        return true;
    }

    private static float[] unitFeature(User row) {
        float[] vector = FaceUtils.parseFeatureArray(row.getFeatureData());
        return FaceUtils.normalizeInPlace(vector) ? vector : null;
    }

    long usedBytes() { return usedBytes.get(); }
    int cachedSize(long groupId) { Group g = groups.get(groupId); return g == null ? -1 : g.size; }

    /** Rows live in slots [0, size); a removal moves the last row into the hole. */
    private final class Group {
        final ReentrantReadWriteLock lock = new ReentrantReadWriteLock();
        final String epoch;
        volatile long revision;
        volatile long lastUsed = System.nanoTime();
        int dim, size;
        long bytes;
        float[] matrix = new float[0];
        long[] ids = new long[0], versions = new long[0];
        final Map<Long, Integer> slots = new HashMap<>();

        Group(String epoch, long revision) { this.epoch = epoch; this.revision = revision; }

        boolean covers(RevisionClock clock) { return epoch.equals(clock.getEpoch()) && revision >= clock.getHead(); }

        /** False when the vector does not fit the memory budget. Rows of another dimension are skipped. */
        boolean put(long id, Long version, float[] vector) {
            if (dim == 0) dim = vector.length;
            if (vector.length != dim) { remove(id); return true; }
            Integer slot = slots.get(id);
            if (slot == null) {
                if (size == ids.length && !grow()) return false;
                slot = size++;
                slots.put(id, slot);
                ids[slot] = id;
            }
            versions[slot] = version == null ? 0 : version;
            System.arraycopy(vector, 0, matrix, slot * dim, dim);
            return true;
        }

        void remove(long id) {
            Integer slot = slots.remove(id);
            if (slot == null) return;
            int last = --size;
            if (slot != last) {
                ids[slot] = ids[last]; versions[slot] = versions[last];
                System.arraycopy(matrix, last * dim, matrix, slot * dim, dim);
                slots.put(ids[slot], slot);
            }
        }

        private boolean grow() { return ensureCapacity(Math.max(64, ids.length * 2L)); }

        boolean ensureCapacity(long wanted) {
            if (wanted <= ids.length) return true;
            if (wanted * dim > Integer.MAX_VALUE - 8) return false;
            int capacity = (int) wanted;
            long added = ((long) capacity - ids.length) * (dim * 4L + 16);
            if (!reserve(this, added)) return false;
            bytes += added;
            matrix = Arrays.copyOf(matrix, capacity * dim);
            ids = Arrays.copyOf(ids, capacity);
            versions = Arrays.copyOf(versions, capacity);
            return true;
        }

        /** Same conventions as the database scan: only positive scores count, ties go to the lower id. */
        Top2 scan(float[] probe) {
            Long bestId = null, bestVersion = null;
            float best = 0, second = 0;
            if (probe.length == dim) {
                for (int i = 0; i < size; i++) {
                    float s = FaceUtils.dot(probe, matrix, i * dim);
                    if (!Float.isFinite(s)) continue;
                    if (s > best || (s == best && bestId != null && ids[i] < bestId)) {
                        if (bestId != null) second = best;
                        best = s; bestId = ids[i]; bestVersion = versions[i];
                    } else if (s > second) {
                        second = s;
                    }
                }
            }
            return new Top2(bestId, bestVersion, best, second);
        }
    }

    /** Ids in group {@code groupId} as cached, for tests. */
    List<Long> cachedIds(long groupId) {
        Group g = groups.get(groupId);
        List<Long> out = new ArrayList<>();
        if (g != null) for (int i = 0; i < g.size; i++) out.add(g.ids[i]);
        return out;
    }
}
