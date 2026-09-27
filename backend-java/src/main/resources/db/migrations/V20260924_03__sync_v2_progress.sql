-- Explicit MySQL 8 migration; run after the runtime/sync schema migration.
SET @has_column=(SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='sync_lease' AND column_name='scan_cursor');
SET @ddl=IF(@has_column>0,'SELECT 1','ALTER TABLE sync_lease ADD COLUMN scan_cursor BIGINT NOT NULL DEFAULT 0');
PREPARE sync_v2_statement FROM @ddl;
EXECUTE sync_v2_statement;
DEALLOCATE PREPARE sync_v2_statement;
SET @has_column=(SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='sync_lease' AND column_name='scan_complete');
SET @ddl=IF(@has_column>0,'SELECT 1','ALTER TABLE sync_lease ADD COLUMN scan_complete TINYINT NOT NULL DEFAULT 0');
PREPARE sync_v2_statement FROM @ddl;
EXECUTE sync_v2_statement;
DEALLOCATE PREPARE sync_v2_statement;
CREATE TABLE IF NOT EXISTS sync_device_cursor (
    device_sn VARCHAR(64) PRIMARY KEY,
    scope_version BIGINT NOT NULL,
    epoch CHAR(36) NOT NULL,
    ack_revision BIGINT NOT NULL,
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB;
