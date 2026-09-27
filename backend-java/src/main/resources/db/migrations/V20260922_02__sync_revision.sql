-- Run explicitly against a verified, backed up target. No application auto migration.
SET @has_column = (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='sys_user' AND column_name='sync_version');
SET @ddl = IF(@has_column=0,'ALTER TABLE sys_user ADD COLUMN sync_version BIGINT NOT NULL DEFAULT 0','SELECT 1');
PREPARE statement FROM @ddl;
EXECUTE statement;
DEALLOCATE PREPARE statement;
CREATE TABLE IF NOT EXISTS sync_revision (id TINYINT PRIMARY KEY,epoch CHAR(36) NOT NULL,head BIGINT NOT NULL DEFAULT 0,retention_floor BIGINT NOT NULL DEFAULT 0) ENGINE=InnoDB;
INSERT IGNORE INTO sync_revision(id,epoch,head,retention_floor) VALUES (1,UUID(),0,0);
CREATE TABLE IF NOT EXISTS user_change_log (revision BIGINT PRIMARY KEY,user_id BIGINT NOT NULL,old_group_id BIGINT NULL,new_group_id BIGINT NULL,operation VARCHAR(8) NOT NULL,postimage LONGTEXT NULL,created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),INDEX idx_change_created(created_at,revision)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS sync_lease (id CHAR(36) PRIMARY KEY,device_sn VARCHAR(64) NOT NULL,scope_version BIGINT NOT NULL,epoch CHAR(36) NOT NULL,start_revision BIGINT NOT NULL,max_id BIGINT NOT NULL,upper_revision BIGINT NULL,expires_at DATETIME(3) NOT NULL,INDEX idx_lease_expiry(expires_at)) ENGINE=InnoDB;
