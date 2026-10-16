package com.bonuos.face.service;

import com.bonuos.face.dto.RevisionClock;
import com.bonuos.face.entity.User;
import com.bonuos.face.mapper.FaceSearchMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.test.context.ActiveProfiles;
import javax.sql.DataSource;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;

/** The gallery's SQL (clock, change replay, rows by id) against a real schema. */
@SpringBootTest @ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test(?:\\?.*)?")
class MySqlFaceGalleryTest {
    @Autowired JdbcTemplate jdbc; @Autowired DataSource dataSource;
    @Autowired UserMutationService mutations; @Autowired FaceSearchMapper mapper;
    long groupA, groupB;

    @BeforeEach void isolatedFixture() {
        assertEquals("face_test",jdbc.queryForObject("SELECT DATABASE()",String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql"),new ClassPathResource("db/migrations/V20260922_02__sync_revision.sql")).execute(dataSource);
        jdbc.update("DELETE FROM sys_user"); jdbc.update("DELETE FROM user_change_log"); jdbc.update("DELETE FROM sys_user_group");
        jdbc.update("UPDATE sync_revision SET head=0,retention_floor=0,epoch=UUID() WHERE id=1");
        jdbc.update("INSERT INTO sys_user_group(group_no,group_name) VALUES ('ga','A'),('gb','B')");
        groupA = jdbc.queryForObject("SELECT id FROM sys_user_group WHERE group_no='ga'",Long.class);
        groupB = jdbc.queryForObject("SELECT id FROM sys_user_group WHERE group_no='gb'",Long.class);
    }

    private User add(String name, long group, String feature) {
        User u=new User(); u.setUserNo(UUID.randomUUID().toString()); u.setName(name); u.setGroupId(group); u.setStatus(1);
        u.setFeatureData(feature); u.setPhotoUrl(name+".jpg"); u.setCreateTime(LocalDateTime.now()); u.setUpdateTime(LocalDateTime.now());
        return mutations.insert(u);
    }
    private FaceGallery gallery() { return new FaceGallery(mapper, 1 << 20, 100, new FaceGalleryTest.Direct()); }
    private FaceGallery.Top2 search(FaceGallery g, long group, float... probe) {
        FaceGallery.Top2 top = g.search(group, mapper.clock(), probe);
        return top != null ? top : g.search(group, mapper.clock(), probe);
    }

    @Test void clockMapsTheRevisionRow() {
        jdbc.update("UPDATE sync_revision SET head=7,retention_floor=3 WHERE id=1");
        RevisionClock clock = mapper.clock();
        assertEquals(7, clock.getHead()); assertEquals(3, clock.getRetentionFloor());
        assertEquals(mapper.revision(), clock.token());
    }

    @Test void replaysAddsEditsMovesAndDeletesWithoutRebuilding() {
        User a = add("a", groupA, "[1,0]"); User b = add("b", groupA, "[0,1]"); add("c", groupB, "[1,0]");
        FaceGallery g = gallery();
        assertEquals(a.getId(), search(g, groupA, 1, 0).bestId());
        assertEquals(2, g.cachedSize(groupA));

        User d = add("d", groupA, "[0.6,0.8]");
        mutations.update(b.getId(), "b", groupA, "b2.jpg", "[1,0.01]");     // re-enrolled
        mutations.update(a.getId(), "a", groupB, null, null);                // moved out
        assertEquals(List.of(a.getId(), b.getId(), d.getId()),
                mapper.changedUsers(groupA, 3, mapper.clock().getHead(), 101).stream().sorted().toList());

        FaceGallery.Top2 top = g.search(groupA, mapper.clock(), new float[]{1, 0});
        assertNotNull(top, "a short backlog is replayed in place");
        assertEquals(b.getId(), top.bestId());
        assertEquals(jdbc.queryForObject("SELECT sync_version FROM sys_user WHERE id=?", Long.class, b.getId()), top.bestVersion());
        assertEquals(List.of(b.getId(), d.getId()), g.cachedIds(groupA).stream().sorted().toList());

        mutations.delete(b.getId());
        assertEquals(d.getId(), g.search(groupA, mapper.clock(), new float[]{1, 0}).bestId());
        assertEquals(List.of(d.getId()), g.cachedIds(groupA));
    }

    @Test void trimmedLogForcesARebuild() {
        add("a", groupA, "[1,0]");
        FaceGallery g = gallery();
        search(g, groupA, 1, 0);
        User b = add("b", groupA, "[0,1]");
        jdbc.update("DELETE FROM user_change_log");
        jdbc.update("UPDATE sync_revision SET retention_floor=head WHERE id=1");
        assertNull(g.search(groupA, mapper.clock(), new float[]{0, 1}));
        assertEquals(b.getId(), search(g, groupA, 0, 1).bestId());
    }
}
