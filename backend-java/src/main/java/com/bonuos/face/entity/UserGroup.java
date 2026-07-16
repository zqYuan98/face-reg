package com.bonuos.face.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("sys_user_group")
public class UserGroup {
    @TableId(type = IdType.AUTO)
    private Long id;
    private String groupNo;
    private String groupName;
    private String description;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
