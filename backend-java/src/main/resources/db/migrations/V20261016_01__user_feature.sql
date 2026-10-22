-- Features of versions other than recognizer_v1_1024 (which stays in sys_user.feature_data).
-- photo_url records the photo a feature came from: a row whose photo differs from sys_user.photo_url
-- is stale and is re-extracted by the feature backfill.
CREATE TABLE IF NOT EXISTS sys_user_feature (
    user_id BIGINT NOT NULL,
    feature_version VARCHAR(32) NOT NULL,
    feature_dim SMALLINT NOT NULL,
    feature_blob VARBINARY(8192) NOT NULL COMMENT 'little-endian float32, unit length',
    photo_url VARCHAR(255) NOT NULL,
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, feature_version),
    INDEX idx_feature_version (feature_version, user_id),
    CONSTRAINT fk_user_feature_user FOREIGN KEY (user_id) REFERENCES sys_user(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人员多版本人脸特征';
