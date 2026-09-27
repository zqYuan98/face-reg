package com.bonuos.face.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.bonuos.face.entity.Device;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface DeviceMapper extends BaseMapper<Device> {
    @org.apache.ibatis.annotations.Insert("INSERT INTO sys_device(device_sn,device_name,ip_address,status,last_heartbeat,create_time) VALUES(#{sn},'Unknown Device',#{ip},1,NOW(),NOW()) ON DUPLICATE KEY UPDATE ip_address=VALUES(ip_address),last_heartbeat=NOW(),status=1")
    int heartbeat(@org.apache.ibatis.annotations.Param("sn") String sn, @org.apache.ibatis.annotations.Param("ip") String ip);
}
