package com.bonuos.face.dto;

import java.util.List;

public record GroupOptionsResult(List<GroupOption> items, Long nextCursor, boolean hasMore) {
    public GroupOptionsResult {
        items = List.copyOf(items);
    }
}
