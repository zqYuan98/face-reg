SET @has_column = (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='user_change_log' AND column_name='photo_url');
SET @ddl = IF(@has_column=0,'ALTER TABLE user_change_log ADD COLUMN photo_url VARCHAR(255) NULL, ADD INDEX idx_change_photo(photo_url)','SELECT 1');
PREPARE statement FROM @ddl;
EXECUTE statement;
DEALLOCATE PREPARE statement;
-- This release introduces the log. If upgrading a preview that already has events, retain their photos.
UPDATE user_change_log SET photo_url=JSON_UNQUOTE(JSON_EXTRACT(postimage,'$.photoUrl')) WHERE photo_url IS NULL AND postimage IS NOT NULL AND JSON_VALID(postimage) AND JSON_EXTRACT(postimage,'$.photoUrl') IS NOT NULL;
