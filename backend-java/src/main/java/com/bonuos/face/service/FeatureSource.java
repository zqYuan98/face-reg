package com.bonuos.face.service;

import com.bonuos.face.dto.FeatureRow;
import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.FaceSearchMapper;
import com.bonuos.face.mapper.UserFeatureMapper;
import com.bonuos.face.util.FaceUtils;
import com.bonuos.face.util.FeatureVersions;

import java.util.Collection;
import java.util.List;

/** Where the search reads one feature version: v1 from sys_user.feature_data, others from sys_user_feature. */
public interface FeatureSource {
    /** {@code feature} is the stored vector (not necessarily unit length), or null when unusable. */
    record Row(long id, Long groupId, Integer status, Long syncVersion, float[] feature) {}

    String version();
    /** Active members of the group that have a feature row, by ascending id. */
    List<Row> nextChunk(long groupId, long afterId, int limit);
    long countActive(long groupId);
    /** Current rows of these people whatever their status or group; feature null when they have none. */
    List<Row> rowsByIds(Collection<Long> ids);

    static FeatureSource of(String version, FaceSearchMapper users, UserFeatureMapper features) {
        return FeatureVersions.V1.equals(FeatureVersions.require(version)) ? v1(users) : stored(version, features);
    }

    static FeatureSource v1(FaceSearchMapper users) {
        return new FeatureSource() {
            public String version() { return FeatureVersions.V1; }
            public List<Row> nextChunk(long groupId, long afterId, int limit) {
                return users.nextChunk(groupId, afterId, limit).stream().map(FeatureSource::fromUser).toList();
            }
            public long countActive(long groupId) { return users.countActive(groupId); }
            public List<Row> rowsByIds(Collection<Long> ids) {
                return users.rowsByIds(ids).stream().map(FeatureSource::fromUser).toList();
            }
        };
    }

    static FeatureSource stored(String version, UserFeatureMapper features) {
        int dimension = FeatureVersions.dimension(version);
        return new FeatureSource() {
            public String version() { return version; }
            public List<Row> nextChunk(long groupId, long afterId, int limit) {
                return features.nextChunk(version, groupId, afterId, limit).stream().map(r -> fromStored(r, dimension)).toList();
            }
            public long countActive(long groupId) { return features.countActive(version, groupId); }
            public List<Row> rowsByIds(Collection<Long> ids) {
                return features.rowsByIds(version, ids).stream().map(r -> fromStored(r, dimension)).toList();
            }
        };
    }

    private static Row fromUser(User u) {
        return new Row(u.getId(), u.getGroupId(), u.getStatus(), u.getSyncVersion(), FaceUtils.parseFeatureArray(u.getFeatureData()));
    }

    private static Row fromStored(FeatureRow r, int dimension) {
        float[] feature = FaceUtils.fromBlob(r.getFeatureBlob());
        return new Row(r.getId(), r.getGroupId(), r.getStatus(), r.getSyncVersion(),
                feature != null && feature.length == dimension ? feature : null);
    }
}
