package com.bonuos.face.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.bonuos.face.entity.Device;
import com.bonuos.face.mapper.DeviceMapper;
import com.bonuos.face.service.DeviceService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
public class DeviceServiceImpl implements DeviceService {

    @Autowired
    private DeviceMapper deviceMapper;

    @Override
    public Device registerOrHeartbeat(String deviceSn, String ipAddress) {
        LambdaQueryWrapper<Device> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(Device::getDeviceSn, deviceSn);
        Device device = deviceMapper.selectOne(queryWrapper);

        if (device == null) {
            // 注册新设备
            device = new Device();
            device.setDeviceSn(deviceSn);
            device.setDeviceName("Unknown Device");
            device.setIpAddress(ipAddress);
            device.setStatus(1);
            device.setCreateTime(LocalDateTime.now());
            device.setLastHeartbeat(LocalDateTime.now());
            deviceMapper.insert(device);
        } else {
            // 更新心跳
            device.setIpAddress(ipAddress);
            device.setLastHeartbeat(LocalDateTime.now());
            device.setStatus(1);
            deviceMapper.updateById(device);
        }
        return device;
    }
}
