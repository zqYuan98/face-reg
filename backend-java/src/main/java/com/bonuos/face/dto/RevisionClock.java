package com.bonuos.face.dto;

import lombok.Data;

/** The sync_revision row: every personnel write bumps {@code head} and logs one change. */
@Data
public class RevisionClock {
    private String epoch;
    private long head;
    private long retentionFloor;

    public String token() { return epoch + ":" + head; }
}
