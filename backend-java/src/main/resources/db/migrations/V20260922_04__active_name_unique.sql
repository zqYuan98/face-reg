-- Review this result before migration. Never merge or delete duplicate people automatically.
SELECT COALESCE(group_id,0) AS name_scope,name,COUNT(*) AS duplicate_count
FROM sys_user WHERE status=1 GROUP BY COALESCE(group_id,0),name HAVING COUNT(*)>1;
-- Atomic ALTER fails if duplicates remain. Disabled rows don't reserve an active name.
SET @has_unique = (SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='sys_user' AND index_name='uk_user_active_group_name');
SET @ddl = IF(@has_unique=0,'ALTER TABLE sys_user ADD UNIQUE INDEX uk_user_active_group_name ((IF(status=1,COALESCE(group_id,0),NULL)),(IF(status=1,name,NULL)))','SELECT 1');
PREPARE statement FROM @ddl;
EXECUTE statement;
DEALLOCATE PREPARE statement;
