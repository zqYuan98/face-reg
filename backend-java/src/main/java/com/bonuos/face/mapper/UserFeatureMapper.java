package com.bonuos.face.mapper;

import com.bonuos.face.dto.FeatureRow;
import org.apache.ibatis.annotations.*;
import java.util.Collection;
import java.util.List;

/** sys_user_feature: features of versions other than recognizer_v1_1024. */
@Mapper
public interface UserFeatureMapper {
    @Select("SELECT u.id, u.sync_version, f.feature_blob FROM sys_user u JOIN sys_user_feature f ON f.user_id = u.id AND f.feature_version = #{version} "
            + "WHERE u.group_id = #{groupId} AND u.status = 1 AND u.id > #{afterId} ORDER BY u.id LIMIT #{limit}")
    List<FeatureRow> nextChunk(@Param("version") String version, @Param("groupId") long groupId, @Param("afterId") long afterId, @Param("limit") int limit);

    @Select("SELECT COUNT(*) FROM sys_user u JOIN sys_user_feature f ON f.user_id = u.id AND f.feature_version = #{version} "
            + "WHERE u.group_id = #{groupId} AND u.status = 1")
    long countActive(@Param("version") String version, @Param("groupId") long groupId);

    @Select("<script>SELECT u.id, u.group_id, u.status, u.sync_version, f.feature_blob FROM sys_user u "
            + "LEFT JOIN sys_user_feature f ON f.user_id = u.id AND f.feature_version = #{version} WHERE u.id IN "
            + "<foreach collection='ids' item='id' open='(' separator=',' close=')'>#{id}</foreach></script>")
    List<FeatureRow> rowsByIds(@Param("version") String version, @Param("ids") Collection<Long> ids);

    /** Active people with a photo but no feature of {@code version} for that photo, after {@code afterId}. */
    @Select("SELECT u.id, u.group_id, u.photo_url FROM sys_user u LEFT JOIN sys_user_feature f ON f.user_id = u.id AND f.feature_version = #{version} "
            + "WHERE u.status = 1 AND u.photo_url IS NOT NULL AND u.id > #{afterId} AND (f.user_id IS NULL OR f.photo_url <> u.photo_url) "
            + "ORDER BY u.id LIMIT #{limit}")
    List<FeatureRow> missing(@Param("version") String version, @Param("afterId") long afterId, @Param("limit") int limit);

    @Select("SELECT COUNT(*) FROM sys_user u WHERE u.status = 1 AND u.photo_url IS NOT NULL")
    long countEnrollable();

    @Select("SELECT COUNT(*) FROM sys_user u JOIN sys_user_feature f ON f.user_id = u.id AND f.feature_version = #{version} AND f.photo_url = u.photo_url "
            + "WHERE u.status = 1")
    long countCurrent(@Param("version") String version);
}
