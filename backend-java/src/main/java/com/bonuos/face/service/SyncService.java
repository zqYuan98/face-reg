package com.bonuos.face.service;

import com.bonuos.face.entity.User;
import java.util.List;

public interface SyncService {
    /**
     * 获取所有需要同步的用户数据
     * 
     * @param lastSyncTime 上次同步时间戳 (可选，用于增量同步)
     * @return 用户列表
     */
    List<User> getSyncData(Long lastSyncTime);
}
