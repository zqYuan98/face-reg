package com.bonuos.face.service;

import com.bonuos.face.entity.Device;

public interface DeviceService {
    /**
     * 设备注册/心跳
     * 
     * @param deviceSn  设备序列号
     * @param ipAddress IP地址
     * @return 设备信息
     */
    Device registerOrHeartbeat(String deviceSn, String ipAddress);
}
