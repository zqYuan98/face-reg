package com.bonuos.face.dto;

import java.util.List;

public record PageResult<T>(List<T> records, long total, long page, long size) {
    public PageResult {
        records = List.copyOf(records);
    }
}
