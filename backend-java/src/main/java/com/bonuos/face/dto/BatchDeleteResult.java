package com.bonuos.face.dto;

import java.util.List;

public record BatchDeleteResult(int requestedCount, int uniqueCount,
        List<Long> successfulIds, List<Long> alreadyAbsentIds, int deletedCount,
        List<Failure> failures) {
    public BatchDeleteResult {
        successfulIds = List.copyOf(successfulIds);
        alreadyAbsentIds = List.copyOf(alreadyAbsentIds);
        failures = List.copyOf(failures);
    }

    public int getSuccessCount() { return successfulIds.size(); }
    public int getFailureCount() { return failures.size(); }

    public record Failure(Long id, String errorCode, String message) {}
}
