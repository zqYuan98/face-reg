package com.bonuos.face.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.bonuos.face.dto.GroupOption;
import com.bonuos.face.dto.GroupOptionsResult;
import com.bonuos.face.dto.PageResult;
import com.bonuos.face.dto.UserListItem;
import com.bonuos.face.entity.User;
import com.bonuos.face.entity.UserGroup;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.AdminQueryMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class AdminQueryService {
    private static final int LEGACY_LIMIT = 100;
    private static final long MAX_OFFSET = 1_000_000;
    private final AdminQueryMapper mapper;
    private final AdminUserPageReader users;

    public AdminQueryService(AdminQueryMapper mapper, AdminUserPageReader users) {
        this.mapper = mapper;
        this.users = users;
    }

    public PageResult<UserListItem> pageUsers(long page, long size, String name, Long groupId) {
        Page<Long> request = pageRequest(page, size);
        validateGroupId(groupId);
        // Validation completes before entering the reader's transaction/acquiring a DB connection.
        return users.read(request, containsPattern(name), groupId);
    }

    public PageResult<UserGroup> pageGroups(long page, long size, String keyword) {
        Page<UserGroup> request = pageRequest(page, size);
        IPage<UserGroup> result = mapper.selectGroupPage(request, containsPattern(keyword));
        return new PageResult<>(result.getRecords(), result.getTotal(), page, size);
    }

    public GroupOptionsResult groupOptions(String keyword, long afterId, int limit) {
        if (afterId < 0) {
            throw invalid("afterId 必须大于或等于 0");
        }
        if (limit < 1 || limit > 50) {
            throw invalid("limit 必须在 1 到 50 之间");
        }
        List<GroupOption> fetched = mapper.selectGroupOptions(containsPattern(keyword), afterId, limit + 1);
        boolean hasMore = fetched.size() > limit;
        List<GroupOption> items = hasMore ? fetched.subList(0, limit) : fetched;
        Long nextCursor = hasMore ? items.get(items.size() - 1).id() : null;
        return new GroupOptionsResult(items, nextCursor, hasMore);
    }

    public long countGroups() {
        return mapper.countGroups();
    }

    public List<User> legacyUsers(String name, Long groupId) {
        validateGroupId(groupId);
        List<Long> ids = mapper.selectLegacyUserIds(containsPattern(name), groupId);
        checkLegacyLimit(ids);
        return ids.isEmpty() ? List.of() : mapper.selectLegacyUsersByIds(ids);
    }

    public List<UserGroup> legacyGroups() {
        List<Long> ids = mapper.selectLegacyGroupIds();
        checkLegacyLimit(ids);
        return ids.isEmpty() ? List.of() : mapper.selectLegacyGroupsByIds(ids);
    }

    private static <T> Page<T> pageRequest(long page, long size) {
        if (page < 1) {
            throw invalid("page 必须大于或等于 1");
        }
        if (size < 1 || size > 100) {
            throw invalid("size 必须在 1 到 100 之间");
        }
        long offset;
        try {
            offset = Math.multiplyExact(page - 1, size);
        } catch (ArithmeticException exception) {
            throw invalid("分页偏移量超出允许范围");
        }
        if (offset > MAX_OFFSET) {
            throw invalid("分页偏移量不能超过 1000000，请缩小查询范围");
        }
        Page<T> request = new Page<>(page, size);
        // Counts run on the same ID/filter query, with no details join to scan for skipped rows.
        request.setOptimizeJoinOfCountSql(false);
        return request;
    }

    private static void validateGroupId(Long groupId) {
        if (groupId != null && groupId <= 0) {
            throw invalid("groupId 必须为正整数");
        }
    }

    private static String containsPattern(String keyword) {
        if (keyword == null) {
            return null;
        }
        String trimmed = keyword.trim();
        if (trimmed.length() > 100) {
            throw invalid("关键词不能超过 100 个字符");
        }
        if (trimmed.isEmpty()) {
            return null;
        }
        return "%" + trimmed.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%";
    }

    private static void checkLegacyLimit(List<Long> ids) {
        if (ids.size() > LEGACY_LIMIT) {
            throw new ApiException(HttpStatus.CONFLICT, "PAGINATION_REQUIRED", "请使用分页接口");
        }
    }

    private static ApiException invalid(String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", message);
    }
}
