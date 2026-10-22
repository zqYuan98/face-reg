package com.bonuos.face.service;

import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.mapper.FaceSearchMapper;
import com.bonuos.face.mapper.UserFeatureMapper;
import com.bonuos.face.util.FaceFeatureExtractorClient;
import com.bonuos.face.util.FaceUtils;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.test.context.ActiveProfiles;
import javax.sql.DataSource;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** sys_user_feature writes, the arcface_r50_512 gallery and the backfill against a real schema. */
@SpringBootTest @ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test(?:\\?.*)?")
class MySqlUserFeatureTest {
    static final String V2 = "arcface_r50_512";
    @Autowired JdbcTemplate jdbc; @Autowired DataSource dataSource;
    @Autowired UserMutationService mutations; @Autowired FaceSearchMapper mapper; @Autowired UserFeatureMapper features; @Autowired UserFeatureStore store;
    long group;

    @BeforeEach void isolatedFixture() {
        assertEquals("face_test",jdbc.queryForObject("SELECT DATABASE()",String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql"),new ClassPathResource("db/migrations/V20260922_02__sync_revision.sql"),
                new ClassPathResource("db/migrations/V20261016_01__user_feature.sql")).execute(dataSource);
        jdbc.update("DELETE FROM sys_user_feature"); jdbc.update("DELETE FROM sys_user"); jdbc.update("DELETE FROM user_change_log");
        jdbc.update("DELETE FROM sys_user_group");
        jdbc.update("UPDATE sync_revision SET head=0,retention_floor=0,epoch=UUID() WHERE id=1");
        jdbc.update("INSERT INTO sys_user_group(group_no,group_name) VALUES ('g','G')");
        group = jdbc.queryForObject("SELECT id FROM sys_user_group WHERE group_no='g'",Long.class);
    }

    static float[] unit(int axis) { float[] v = new float[512]; v[axis] = 1; return v; }
    private User add(String name, String photo, Map<String, float[]> extra) {
        User u=new User(); u.setUserNo(UUID.randomUUID().toString()); u.setName(name); u.setGroupId(group); u.setStatus(1);
        u.setFeatureData("[0.1,0.2]"); u.setPhotoUrl(photo); u.setCreateTime(LocalDateTime.now()); u.setUpdateTime(LocalDateTime.now());
        return mutations.insert(u, extra);
    }
    private float[] storedV2(long id) {
        List<byte[]> blobs = jdbc.query("SELECT feature_blob FROM sys_user_feature WHERE user_id=? AND feature_version=?", (rs,n)->rs.getBytes(1), id, V2);
        return blobs.isEmpty() ? null : FaceUtils.fromBlob(blobs.get(0));
    }

    @Test void enrolmentWritesReplacesAndCascades() {
        User a = add("a", "a.jpg", Map.of(V2, unit(0)));
        assertArrayEquals(unit(0), storedV2(a.getId()));
        mutations.update(a.getId(), "a", group, "a2.jpg", "[0.3,0.4]", Map.of(V2, unit(1)));
        assertArrayEquals(unit(1), storedV2(a.getId()));
        mutations.update(a.getId(), "a", group, "a3.jpg", "[0.3,0.4]", Map.of());
        assertNull(storedV2(a.getId()), "a feature of the previous photo is stale");
        mutations.update(a.getId(), "renamed", group, null, null, Map.of());
        assertEquals(List.of(a.getId()), features.missing(V2, 0, 10).stream().map(r -> r.getId()).toList());
        mutations.delete(a.getId());
        assertEquals(0L, jdbc.queryForObject("SELECT COUNT(*) FROM sys_user_feature", Long.class));
        assertThrows(IllegalArgumentException.class, () -> add("bad", "b.jpg", Map.of(V2, new float[3])));
    }

    @Test void conditionalWriteLosesToReEnrolment() {
        User a = add("a", "a.jpg", Map.of());
        assertEquals(1, features.missing(V2, 0, 10).size());
        assertEquals(0, store.putIfPhotoCurrent(a.getId(), V2, 512, FaceUtils.toBlob(unit(2)), "old.jpg"));
        assertEquals(1, store.putIfPhotoCurrent(a.getId(), V2, 512, FaceUtils.toBlob(unit(2)), "a.jpg"));
        assertEquals(1, features.countCurrent(V2)); assertEquals(1, features.countEnrollable());
        assertTrue(features.missing(V2, 0, 10).isEmpty());
    }

    @Test void v2GalleryReplaysReEnrolmentThroughTheChangeLog() {
        User a = add("a", "a.jpg", Map.of(V2, unit(0))); User b = add("b", "b.jpg", Map.of(V2, unit(1)));
        FaceGallery g = new FaceGallery(mapper, FeatureSource.stored(V2, features), 1 << 20, 100, new FaceGalleryTest.Direct());
        assertNull(g.search(group, mapper.clock(), unit(0)));
        assertEquals(a.getId(), g.search(group, mapper.clock(), unit(0)).bestId());
        mutations.update(b.getId(), "b", group, "b2.jpg", "[0.1]", Map.of(V2, unit(0)));
        FaceGallery.Top2 top = g.search(group, mapper.clock(), unit(0));
        assertNotNull(top, "replayed, not rebuilt");
        assertEquals(1f, top.best(), 1e-6); assertEquals(1f, top.second(), 1e-6);
    }

    @Test void backfillWritesMissingFeaturesAndReportsFailures(@org.junit.jupiter.api.io.TempDir Path dir) throws Exception {
        User a = add("a", "a.jpg", Map.of()); User b = add("b", "b.jpg", Map.of()); User c = add("c", "gone.jpg", Map.of());
        User d = add("d", "d.jpg", Map.of(V2, unit(5)));
        PhotoStorage photos = mock(PhotoStorage.class);
        for (String name : List.of("a.jpg", "b.jpg")) when(photos.published(name)).thenReturn(Files.write(dir.resolve(name), new byte[]{1}));
        FaceFeatureExtractorClient extractor = mock(FaceFeatureExtractorClient.class);
        when(extractor.extractFeature(argThat((File f) -> f != null && f.getName().equals("a.jpg")), eq(V2))).thenReturn(unit(3));
        when(extractor.extractFeature(argThat((File f) -> f != null && f.getName().equals("b.jpg")), eq(V2)))
                .thenThrow(new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "NO_USABLE_FACE", "x"));
        FaceSearchService search = mock(FaceSearchService.class);
        FeatureBackfillService backfill = new FeatureBackfillService(features, store, photos, extractor, search, 2);
        assertTrue(backfill.start(V2));
        for (int i = 0; i < 100 && backfill.status(V2).running(); i++) Thread.sleep(20);
        FeatureBackfillService.Status status = backfill.status(V2);
        assertFalse(status.running());
        assertEquals(3, status.processed()); assertEquals(1, status.written()); assertEquals(2, status.failed());
        assertEquals(4, status.enrollable()); assertEquals(2, status.current());
        assertArrayEquals(unit(3), storedV2(a.getId()));
        assertEquals(List.of(b.getId(), c.getId()), status.recentFailures().stream().map(FeatureBackfillService.Failure::userId).sorted().toList());
        verify(search).featuresChanged(V2, group);
        assertArrayEquals(unit(5), storedV2(d.getId()));
        assertThrows(ApiException.class, () -> backfill.start("recognizer_v1_1024"));
    }
}
