package com.bonuos.face.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.bonuos.face.entity.User;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Param;

@Mapper
public interface UserMapper extends BaseMapper<User> {
    @Select("SELECT id, photo_url FROM sys_user WHERE id = #{id}")
    User selectDeleteMetadata(@Param("id") Long id);

    @Select("SELECT COUNT(*) FROM user_change_log WHERE photo_url = #{filename} OR old_photo_url = #{filename}")
    long countRetainedPhotoReferences(@Param("filename") String filename);
}
