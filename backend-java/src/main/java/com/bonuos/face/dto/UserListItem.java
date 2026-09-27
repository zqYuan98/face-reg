package com.bonuos.face.dto;

import java.time.LocalDateTime;

public record UserListItem(
        Long id,
        String userNo,
        String name,
        Long groupId,
        String groupName,
        String photoUrl,
        boolean hasFeature,
        Integer status,
        LocalDateTime createTime,
        LocalDateTime updateTime) {
}
