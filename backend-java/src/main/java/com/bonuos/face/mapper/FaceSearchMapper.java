package com.bonuos.face.mapper;

import com.bonuos.face.entity.User;
import org.apache.ibatis.annotations.*;
import java.util.List;

@Mapper
public interface FaceSearchMapper {
    @Select("SELECT CONCAT(epoch, ':', head) FROM sync_revision WHERE id=1")
    String revision();
    @Select("SELECT id, feature_data, sync_version FROM sys_user WHERE group_id = #{groupId} AND status = 1 AND id > #{afterId} ORDER BY id LIMIT #{limit}")
    List<User> nextChunk(@Param("groupId") long groupId, @Param("afterId") long afterId, @Param("limit") int limit);
}
