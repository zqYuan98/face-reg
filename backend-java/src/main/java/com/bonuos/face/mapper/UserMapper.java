package com.bonuos.face.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.bonuos.face.entity.User;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface UserMapper extends BaseMapper<User> {
}
