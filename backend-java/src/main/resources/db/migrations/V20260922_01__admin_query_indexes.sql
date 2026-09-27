-- MySQL 8. Run explicitly using a migration account in the intended database.
-- This adds indexes only. Check EXPLAIN and disk/online-DDL capacity before production.
-- Equivalent full-column indexes are retained regardless of their names.
SET @has_index = (SELECT COUNT(*) FROM (
    SELECT index_name FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'sys_user'
    GROUP BY index_name
    HAVING GROUP_CONCAT(column_name ORDER BY seq_in_index) = 'status,create_time,id'
       AND SUM(sub_part IS NOT NULL) = 0
) existing_indexes);
SET @ddl = IF(@has_index > 0, 'SELECT 1',
    'ALTER TABLE sys_user ADD INDEX idx_user_status_created (status, create_time, id), ALGORITHM=INPLACE, LOCK=NONE');
PREPARE admin_index_statement FROM @ddl;
EXECUTE admin_index_statement;
DEALLOCATE PREPARE admin_index_statement;

SET @has_index = (SELECT COUNT(*) FROM (
    SELECT index_name FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'sys_user'
    GROUP BY index_name
    HAVING GROUP_CONCAT(column_name ORDER BY seq_in_index) = 'group_id,status,create_time,id'
       AND SUM(sub_part IS NOT NULL) = 0
) existing_indexes);
SET @ddl = IF(@has_index > 0, 'SELECT 1',
    'ALTER TABLE sys_user ADD INDEX idx_user_group_status_created (group_id, status, create_time, id), ALGORITHM=INPLACE, LOCK=NONE');
PREPARE admin_index_statement FROM @ddl;
EXECUTE admin_index_statement;
DEALLOCATE PREPARE admin_index_statement;
