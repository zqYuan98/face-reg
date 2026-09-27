package com.bonuos.face.dto;

import com.bonuos.face.entity.User;
import java.util.List;

public final class SyncV2Dtos {
    private SyncV2Dtos() {}
    public record State(String epoch, long head, long retentionFloor, long scopeVersion) {}
    public record Bootstrap(String leaseId, String epoch, long scopeVersion, long startRevision,
                            long maxId, String expiresAt) {}
    public record SnapshotPage(List<User> records, long nextAfterId, boolean hasMore) {
        public SnapshotPage { records = List.copyOf(records); }
    }
    public record UpperBound(String epoch, long scopeVersion, long upperRevision) {}
    public record Event(long revision, long userId, String operation, User user) {}
    public record Changes(List<Event> events, long nextRevision, boolean hasMore, long upperRevision) {
        public Changes { events = List.copyOf(events); }
    }
    public record AckRequest(String epoch, Long scopeVersion, Long revision, String leaseId) {}
    public record Ack(long ackRevision) {}
}
