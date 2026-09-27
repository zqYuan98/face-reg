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
        if (deviceSn == null || deviceSn.isBlank() || deviceSn.length() > 64 || (ipAddress != null && ipAddress.length() > 50))
            throw new com.bonuos.face.exception.ApiException(org.springframework.http.HttpStatus.BAD_REQUEST,"INVALID_DEVICE","设备参数无效");
        deviceMapper.heartbeat(deviceSn,ipAddress);
        return deviceMapper.selectOne(queryWrapper);
    }
}
