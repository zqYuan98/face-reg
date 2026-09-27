-- Validated with 100k synthetic rows. Covers ID paging and literal-contains filtering;
-- contains still scans index entries and is not a constant-time name lookup.
SET @has_index = (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='sys_user' AND index_name='idx_user_status_created_name');
SET @ddl = IF(@has_index=0,'ALTER TABLE sys_user ADD INDEX idx_user_status_created_name(status,create_time,id,name), ALGORITHM=INPLACE, LOCK=NONE','SELECT 1');
PREPARE statement FROM @ddl;
EXECUTE statement;
DEALLOCATE PREPARE statement;
