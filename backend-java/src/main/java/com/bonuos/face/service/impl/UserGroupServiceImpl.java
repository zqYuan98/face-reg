package com.bonuos.face.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.bonuos.face.entity.User;
import com.bonuos.face.entity.UserGroup;
import com.bonuos.face.mapper.UserGroupMapper;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.service.UserGroupService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import cn.hutool.core.util.IdUtil;

import java.time.LocalDateTime;
import java.util.List;

@Service
public class UserGroupServiceImpl implements UserGroupService {

    @Autowired private com.bonuos.face.service.UserMutationService mutations;

    private void validateGroup(UserGroup group) {
        if (group == null || group.getGroupName() == null || group.getGroupName().isBlank() || group.getGroupName().trim().length() > 100 || (group.getDescription() != null && group.getDescription().length() > 255))
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.BAD_REQUEST,"INVALID_GROUP","分组名称必须为 1 至 100 个字符，描述最多 255 个字符");
        group.setGroupName(group.getGroupName().trim());
    }

    @Autowired
    private UserGroupMapper userGroupMapper;

    @Autowired
    private UserMapper userMapper;

    @Override
    public UserGroup addGroup(UserGroup group) {
        validateGroup(group);
        group.setId(null);
        group.setGroupNo(IdUtil.getSnowflakeNextIdStr());
        group.setCreateTime(LocalDateTime.now());
        group.setUpdateTime(LocalDateTime.now());
        userGroupMapper.insert(group);
        return group;
    }

    @Override
    public UserGroup updateGroup(UserGroup group) {
        validateGroup(group);
        if (group.getId() == null) {
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.BAD_REQUEST,"INVALID_GROUP","分组ID不能为空");
        }
        UserGroup existingGroup = userGroupMapper.selectById(group.getId());
        if (existingGroup == null) {
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.NOT_FOUND,"GROUP_NOT_FOUND","分组不存在");
        }

        existingGroup.setGroupName(group.getGroupName());
        existingGroup.setDescription(group.getDescription());
        existingGroup.setUpdateTime(LocalDateTime.now());

        userGroupMapper.updateById(existingGroup);
        return existingGroup;
    }

    @Override
    @org.springframework.transaction.annotation.Transactional(timeout=5)
    public void deleteGroup(Long id) {
        if (id == null || id <= 0) throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.BAD_REQUEST,"INVALID_GROUP","分组ID无效");
        mutations.lockClock();
        // 检查该组下是否有用户
        Long userCount = userMapper.selectCount(new QueryWrapper<User>().eq("group_id", id));
        if (userCount > 0) {
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.CONFLICT,"GROUP_NOT_EMPTY","该分组下存在用户，无法删除");
        }
        userGroupMapper.deleteById(id);
    }

    @Override
    public UserGroup getGroupById(Long id) {
        return userGroupMapper.selectById(id);
    }

    @Override
    public List<UserGroup> listGroups() {
        return userGroupMapper.selectList(null);
    }
}
