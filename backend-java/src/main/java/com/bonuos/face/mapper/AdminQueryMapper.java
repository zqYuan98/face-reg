package com.bonuos.face.mapper;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.bonuos.face.dto.GroupOption;
import com.bonuos.face.dto.UserListItem;
import com.bonuos.face.entity.User;
import com.bonuos.face.entity.UserGroup;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;

@Mapper
public interface AdminQueryMapper {
    IPage<Long> selectUserIdsPage(IPage<Long> page,
                                      @Param("namePattern") String namePattern,
                                      @Param("groupId") Long groupId);

    List<UserListItem> selectUserDetailsByIds(@Param("ids") List<Long> ids);

    IPage<UserGroup> selectGroupPage(IPage<UserGroup> page,
                                     @Param("keywordPattern") String keywordPattern);

    List<GroupOption> selectGroupOptions(@Param("keywordPattern") String keywordPattern,
                                         @Param("afterId") long afterId,
                                         @Param("take") int take);

    long countGroups();

    List<Long> selectLegacyUserIds(@Param("namePattern") String namePattern,
                                    @Param("groupId") Long groupId);

    List<User> selectLegacyUsersByIds(@Param("ids") List<Long> ids);

    List<Long> selectLegacyGroupIds();

    List<UserGroup> selectLegacyGroupsByIds(@Param("ids") List<Long> ids);
}
