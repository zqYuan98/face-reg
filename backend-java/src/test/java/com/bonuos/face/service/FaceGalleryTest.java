package com.bonuos.face.service;

import com.bonuos.face.dto.RevisionClock;
import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.FaceSearchMapper;
import org.junit.jupiter.api.Test;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.TimeUnit;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class FaceGalleryTest {
    /** Runs builds inline so a search after the first one sees the cached group. */
    static final class Direct extends AbstractExecutorService {
        public void execute(Runnable r) { r.run(); }
        public void shutdown() {}
        public List<Runnable> shutdownNow() { return List.of(); }
        public boolean isShutdown() { return false; }
        public boolean isTerminated() { return false; }
        public boolean awaitTermination(long t, TimeUnit u) { return true; }
    }
    static RevisionClock clock(long head) { return clock("e", head, 0); }
    static RevisionClock clock(String epoch, long head, long floor) {
        RevisionClock c = new RevisionClock(); c.setEpoch(epoch); c.setHead(head); c.setRetentionFloor(floor); return c;
    }
    static User row(long id, long group, String feature, long version) {
        User u = new User(); u.setId(id); u.setGroupId(group); u.setStatus(1); u.setFeatureData(feature); u.setSyncVersion(version); return u;
    }
    static FaceGallery gallery(FaceSearchMapper m) { return new FaceGallery(m, 1 << 20, 100, new Direct()); }

    @Test void firstSearchSchedulesBuildThenScansInMemory() {
        FaceSearchMapper m = mock(FaceSearchMapper.class);
        when(m.clock()).thenReturn(clock(5));
        when(m.nextChunk(eq(1L), eq(0L), anyInt())).thenReturn(List.of(row(1,1,"[0,1]",3), row(2,1,"[3,4]",4), row(3,1,"[1,0]",5)));
        FaceGallery g = gallery(m);
        assertNull(g.search(1, clock(5), new float[]{1,0}));
        FaceGallery.Top2 top = g.search(1, clock(5), new float[]{1,0});
        assertEquals(3L, top.bestId()); assertEquals(5L, top.bestVersion());
        assertEquals(1f, top.best(), 1e-6); assertEquals(0.6f, top.second(), 1e-6);
        verify(m, times(1)).nextChunk(anyLong(), anyLong(), anyInt());
    }

    @Test void newerRevisionReplaysOnlyTheGroupsChanges() {
        FaceSearchMapper m = mock(FaceSearchMapper.class);
        when(m.clock()).thenReturn(clock(5));
        when(m.nextChunk(eq(1L), eq(0L), anyInt())).thenReturn(List.of(row(1,1,"[0,1]",1), row(2,1,"[1,0]",2), row(3,1,"[0.6,0.8]",3)));
        FaceGallery g = gallery(m);
        g.search(1, clock(5), new float[]{1,0});
        // 2 deleted, 3 moved to group 9, 4 added, 1 re-enrolled.
        when(m.changedUsers(1, 5, 9, 101)).thenReturn(List.of(1L, 2L, 3L, 4L));
        when(m.rowsByIds(anyCollection())).thenReturn(List.of(row(1,1,"[1,0]",9), row(3,9,"[1,0]",7), row(4,1,"[0.8,0.6]",8)));
        FaceGallery.Top2 top = g.search(1, clock(9), new float[]{1,0});
        assertEquals(1L, top.bestId()); assertEquals(9L, top.bestVersion());
        assertEquals(0.8f, top.second(), 1e-6);
        assertEquals(List.of(1L, 4L), g.cachedIds(1).stream().sorted().toList());
        verify(m, times(1)).nextChunk(anyLong(), anyLong(), anyInt());
    }

    @Test void trimmedLogNewEpochOrLargeBacklogRebuilds() {
        FaceSearchMapper m = mock(FaceSearchMapper.class);
        when(m.nextChunk(eq(1L), eq(0L), anyInt())).thenReturn(List.of(row(1,1,"[1,0]",1)));
        when(m.clock()).thenReturn(clock(5));
        FaceGallery g = gallery(m);
        g.search(1, clock(5), new float[]{1,0});
        when(m.clock()).thenReturn(clock("e", 6, 6));
        assertNull(g.search(1, clock("e", 6, 6), new float[]{1,0}), "log below the cached revision is gone");
        when(m.clock()).thenReturn(clock("f", 6, 0));
        assertNull(g.search(1, clock("f", 6, 0), new float[]{1,0}), "new epoch");
        assertNotNull(g.search(1, clock("f", 6, 0), new float[]{1,0}));
        when(m.clock()).thenReturn(clock("f", 500, 0));
        assertNull(g.search(1, clock("f", 500, 0), new float[]{1,0}), "backlog over max-delta");
        verify(m, never()).changedUsers(anyLong(), anyLong(), anyLong(), anyInt());
    }

    @Test void groupOverBudgetIsNotCachedAndOthersAreEvicted() {
        FaceSearchMapper m = mock(FaceSearchMapper.class);
        when(m.clock()).thenReturn(clock(1));
        List<User> big = new ArrayList<>();
        for (long i = 1; i <= 100; i++) big.add(row(i, 2, "[1,0,0,0]", 1));
        when(m.nextChunk(eq(1L), eq(0L), anyInt())).thenReturn(List.of(row(1,1,"[1,0,0,0]",1)));
        when(m.nextChunk(eq(2L), eq(0L), anyInt())).thenReturn(big);
        when(m.countActive(2L)).thenReturn(100L);
        when(m.nextChunk(eq(3L), eq(0L), anyInt())).thenReturn(List.of(row(7,3,"[1,0,0,0]",1)));
        // 64 slots x (16 + 16) bytes per group: room for two small groups, not for 100 rows.
        FaceGallery g = new FaceGallery(m, 64 * 32 * 2, 100, new Direct());
        g.search(1, clock(1), new float[]{1,0,0,0});
        g.search(2, clock(1), new float[]{1,0,0,0});
        assertEquals(-1, g.cachedSize(2));
        assertNull(g.search(2, clock(1), new float[]{1,0,0,0}));
        verify(m, times(1)).nextChunk(eq(2L), eq(0L), anyInt());
        g.search(3, clock(1), new float[]{1,0,0,0});
        assertEquals(1, g.cachedSize(1)); assertEquals(1, g.cachedSize(3));
        assertEquals(64 * 32 * 2, g.usedBytes());
    }

    @Test void zeroBudgetDisablesTheCache() {
        FaceSearchMapper m = mock(FaceSearchMapper.class);
        FaceGallery g = new FaceGallery(m, 0, 100, new Direct());
        assertNull(g.search(1, clock(1), new float[]{1,0}));
        verifyNoInteractions(m);
    }
}
