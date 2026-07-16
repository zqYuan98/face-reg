package com.bonuos.face.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("sys_user")
public class User {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String userNo;
    private String name;
    private Long groupId;
    private String featureData; // JSON or Base64
    private String photoUrl;
    private Integer status; // 1:Normal, 0:Disabled
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
