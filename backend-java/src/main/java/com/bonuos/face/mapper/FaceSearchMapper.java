package com.bonuos.face.mapper;

import com.bonuos.face.dto.RevisionClock;
import com.bonuos.face.entity.User;
import org.apache.ibatis.annotations.*;
import java.util.Collection;
import java.util.List;

@Mapper
public interface FaceSearchMapper {
    @Select("SELECT CONCAT(epoch, ':', head) FROM sync_revision WHERE id=1")
    String revision();
    @Select("SELECT epoch, head, retention_floor FROM sync_revision WHERE id=1")
    RevisionClock clock();
    @Select("SELECT id, feature_data, sync_version FROM sys_user WHERE group_id = #{groupId} AND status = 1 AND id > #{afterId} ORDER BY id LIMIT #{limit}")
    List<User> nextChunk(@Param("groupId") long groupId, @Param("afterId") long afterId, @Param("limit") int limit);
    @Select("SELECT COUNT(*) FROM sys_user WHERE group_id = #{groupId} AND status = 1")
    long countActive(@Param("groupId") long groupId);
    /** Users whose change touched the group (moved in, moved out, edited or deleted) in (after, until]. */
    @Select("SELECT DISTINCT user_id FROM user_change_log WHERE revision > #{after} AND revision <= #{until} "
            + "AND (old_group_id = #{groupId} OR new_group_id = #{groupId}) LIMIT #{limit}")
    List<Long> changedUsers(@Param("groupId") long groupId, @Param("after") long after, @Param("until") long until, @Param("limit") int limit);
    @Select("<script>SELECT id, group_id, status, feature_data, sync_version FROM sys_user WHERE id IN "
            + "<foreach collection='ids' item='id' open='(' separator=',' close=')'>#{id}</foreach></script>")
    List<User> rowsByIds(@Param("ids") Collection<Long> ids);
}
