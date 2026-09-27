-- 人员组表
CREATE TABLE IF NOT EXISTS sys_user_group (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    group_no VARCHAR(64) NOT NULL UNIQUE COMMENT '分组编号',
    group_name VARCHAR(100) NOT NULL COMMENT '分组名称',
    description VARCHAR(255) COMMENT '描述',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人员分组表';

-- 人员表
CREATE TABLE IF NOT EXISTS sys_user (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    user_no VARCHAR(64) NOT NULL UNIQUE COMMENT '人员编号',
    name VARCHAR(100) NOT NULL COMMENT '姓名',
    group_id BIGINT COMMENT '所属分组ID',
    feature_data TEXT COMMENT '人脸特征数据(JSON或Base64)',
    photo_url VARCHAR(255) COMMENT '照片路径',
    status TINYINT DEFAULT 1 COMMENT '状态 1:正常 0:禁用',
    sync_version BIGINT NOT NULL DEFAULT 0,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_user_status_created (status, create_time, id),
    INDEX idx_user_status_created_name (status, create_time, id, name),
    INDEX idx_user_group_status_created (group_id, status, create_time, id),
    INDEX idx_user_group_status_id (group_id, status, id),
    INDEX idx_user_photo_url (photo_url),
    UNIQUE INDEX uk_user_active_group_name ((IF(status=1,COALESCE(group_id,0),NULL)),(IF(status=1,name,NULL))),
    FOREIGN KEY (group_id) REFERENCES sys_user_group(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人员信息表';

-- 设备表
CREATE TABLE IF NOT EXISTS sys_device (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    device_sn VARCHAR(64) NOT NULL UNIQUE COMMENT '设备序列号',
    device_name VARCHAR(100) COMMENT '设备名称',
    ip_address VARCHAR(50) COMMENT 'IP地址',
    status TINYINT DEFAULT 1 COMMENT '状态 1:在线 0:离线',
    last_heartbeat DATETIME COMMENT '最后心跳时间',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备管理表';

-- 识别记录表
CREATE TABLE IF NOT EXISTS sys_recognition_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    device_id BIGINT COMMENT '设备ID',
    user_id BIGINT COMMENT '识别到的人员ID',
    similarity FLOAT COMMENT '相似度',
    capture_photo VARCHAR(255) COMMENT '抓拍照片路径',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '识别时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='识别记录表';

-- All writers acquire this row before touching synchronized personnel data.
CREATE TABLE IF NOT EXISTS sync_revision (
    id TINYINT PRIMARY KEY,
    epoch CHAR(36) NOT NULL,
    head BIGINT NOT NULL DEFAULT 0,
    retention_floor BIGINT NOT NULL DEFAULT 0
) ENGINE=InnoDB;
INSERT IGNORE INTO sync_revision(id,epoch,head,retention_floor) VALUES (1,UUID(),0,0);

CREATE TABLE IF NOT EXISTS user_change_log (
    revision BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    old_group_id BIGINT NULL,
    new_group_id BIGINT NULL,
    operation VARCHAR(8) NOT NULL,
    postimage LONGTEXT NULL,
    photo_url VARCHAR(255) NULL,
    old_photo_url VARCHAR(255) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_change_created(created_at,revision),
    INDEX idx_change_photo(photo_url),
    INDEX idx_change_old_photo(old_photo_url)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sync_lease (
    id CHAR(36) PRIMARY KEY,
    device_sn VARCHAR(64) NOT NULL,
    scope_version BIGINT NOT NULL,
    epoch CHAR(36) NOT NULL,
    start_revision BIGINT NOT NULL,
    max_id BIGINT NOT NULL,
    upper_revision BIGINT NULL,
    scan_cursor BIGINT NOT NULL DEFAULT 0,
    scan_complete TINYINT NOT NULL DEFAULT 0,
    expires_at DATETIME(3) NOT NULL,
    INDEX idx_lease_expiry(expires_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sync_device_cursor (
    device_sn VARCHAR(64) PRIMARY KEY,
    scope_version BIGINT NOT NULL,
    epoch CHAR(36) NOT NULL,
    ack_revision BIGINT NOT NULL,
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_cursor_updated(epoch,updated_at,ack_revision)
) ENGINE=InnoDB;
