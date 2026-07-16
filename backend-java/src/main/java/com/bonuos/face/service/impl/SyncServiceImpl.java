package com.bonuos.face.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.UserMapper;
import com.bonuos.face.service.SyncService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;

@Service
public class SyncServiceImpl implements SyncService {

    @Autowired
    private UserMapper userMapper;

    @Override
    public List<User> getSyncData(Long lastSyncTime) {
        LambdaQueryWrapper<User> queryWrapper = new LambdaQueryWrapper<>();

        // 如果提供了上次同步时间，则进行增量查询
        if (lastSyncTime != null && lastSyncTime > 0) {
            LocalDateTime dateTime = LocalDateTime.ofInstant(Instant.ofEpochMilli(lastSyncTime),
                    ZoneId.systemDefault());
            queryWrapper.ge(User::getUpdateTime, dateTime);
        }

        // 查询状态为正常的或者刚刚被禁用的（如果需要同步删除状态，逻辑会更复杂，这里简化为同步所有变更）
        return userMapper.selectList(queryWrapper);
    }
}
