package com.bonuos.face.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.bonuos.face.dto.PageResult;
import com.bonuos.face.dto.UserListItem;
import com.bonuos.face.mapper.AdminQueryMapper;
import java.util.List;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

/** Called after validation. Count, paged IDs and current-page details share a consistent snapshot. */
@Service
public class AdminUserPageReader {
    private final AdminQueryMapper mapper;
    public AdminUserPageReader(AdminQueryMapper mapper) { this.mapper = mapper; }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public PageResult<UserListItem> read(Page<Long> request, String namePattern, Long groupId) {
        // Skip deep offsets on a covering index before reading feature length or joining groups.
        IPage<Long> result = mapper.selectUserIdsPage(request, namePattern, groupId);
        List<UserListItem> records = result.getRecords().isEmpty() ? List.of()
                : mapper.selectUserDetailsByIds(result.getRecords());
        return new PageResult<>(records, result.getTotal(), request.getCurrent(), request.getSize());
    }
}
