package com.bonuos.face.service;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/** sys_user_feature writes outside a personnel mutation (the backfill). */
@Repository
public class UserFeatureStore {
    private final JdbcTemplate jdbc;

    public UserFeatureStore(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    /**
     * Writes a feature only while the person is active and still has {@code photoUrl}; returns 0
     * when that changed, so a concurrent re-enrolment is never overwritten by an older photo.
     */
    public int putIfPhotoCurrent(long userId, String version, int dim, byte[] blob, String photoUrl) {
        return jdbc.update("INSERT INTO sys_user_feature(user_id,feature_version,feature_dim,feature_blob,photo_url) "
                + "SELECT id,?,?,?,photo_url FROM sys_user WHERE id=? AND photo_url=? AND status=1 "
                + "ON DUPLICATE KEY UPDATE feature_dim=VALUES(feature_dim),feature_blob=VALUES(feature_blob),photo_url=VALUES(photo_url)",
                version, dim, blob, userId, photoUrl);
    }
}
