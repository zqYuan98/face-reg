package com.bonuos.face.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("sys_device")
public class Device {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String deviceSn;
    private String deviceName;
    private String ipAddress;
    private Integer status; // 1:Online, 0:Offline
    private LocalDateTime lastHeartbeat;
    private LocalDateTime createTime;
}
