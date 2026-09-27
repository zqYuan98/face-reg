package com.bonuos.face.integration;

import com.bonuos.face.service.AdminQueryService;
import com.bonuos.face.exception.ApiException;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.MOCK)
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named = "FACE_TEST_DB_URL", matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test.*")
class MySqlAdminQueryTest {
    @Autowired DataSource dataSource;
    @Autowired JdbcTemplate jdbc;
    @Autowired AdminQueryService queries;
    @Autowired ObjectMapper json;

    @BeforeEach void syntheticFixture() {
        assertEquals("face_test", jdbc.queryForObject("SELECT DATABASE()", String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql")).execute(dataSource);
        jdbc.update("DELETE FROM sys_user");
        jdbc.update("DELETE FROM sys_user_group");
        jdbc.update("INSERT INTO sys_user_group(id,group_no,group_name) VALUES(1,'g1','员工组'),(2,'g2','临时组')");
        for (long id = 1; id <= 21; id++) {
            jdbc.update("INSERT INTO sys_user(id,user_no,name,group_id,feature_data,create_time) VALUES(?,?,?,?,?,?)",
                    id, "test-" + id, id == 1 ? "文字%_!样本" : "人员" + id,
                    id % 2 == 0 ? 1 : 2, "[" + "0.03125,".repeat(1023) + "0.03125]", "2026-09-22 00:00:00");
        }
    }

    @Test void actualMysqlPaginationAndDtoMapping() throws Exception {
        var first = queries.pageUsers(1, 20, null, null);
        var second = queries.pageUsers(2, 20, null, null);
        assertEquals(21, first.total());
        assertEquals(20, first.records().size());
        assertEquals(1, second.records().size());
        assertEquals(21L, first.records().get(0).id());
        assertEquals(1L, second.records().get(0).id());
        assertEquals("临时组", first.records().get(0).groupName());
        assertTrue(first.records().get(0).hasFeature());
        String response = json.writeValueAsString(first);
        assertFalse(response.contains("featureData"));
        assertTrue(response.length() < 15000);
        assertTrue(queries.pageUsers(3, 20, null, null).records().isEmpty());
    }

    @Test void literalLikeAndFilteredCountUseSamePredicates() {
        assertEquals(1, queries.pageUsers(1, 20, "%_!", null).total());
        assertEquals(10, queries.pageUsers(1, 20, null, 1L).total());
        jdbc.update("UPDATE sys_user SET status=0 WHERE id=2");
        assertEquals(9, queries.pageUsers(1, 20, null, 1L).total());
    }

    @Test void legacyBoundaryNeverTruncatesTheMeaningOfCompleteList() {
        for (long id = 22; id <= 100; id++) {
            jdbc.update("INSERT INTO sys_user(id,user_no,name,status) VALUES(?,?,?,1)", id, "test-" + id, "人员" + id);
        }
        assertEquals(100, queries.legacyUsers(null, null).size());
        jdbc.update("INSERT INTO sys_user(id,user_no,name,status) VALUES(101,'test-101','人员101',1)");
        ApiException error = assertThrows(ApiException.class, () -> queries.legacyUsers(null, null));
        assertEquals("PAGINATION_REQUIRED", error.getErrorCode());
        assertEquals(100, queries.pageUsers(1, 100, null, null).records().size());
    }

    @Test void groupCursorCanReachOptionsOutsideTheFirstPage() {
        for (long id = 3; id <= 103; id++) {
            jdbc.update("INSERT INTO sys_user_group(id,group_no,group_name) VALUES(?,?,?)", id, "g" + id, "组选项" + id);
        }
        var first = queries.groupOptions(null, 0, 50);
        var second = queries.groupOptions(null, first.nextCursor(), 50);
        var last = queries.groupOptions(null, second.nextCursor(), 50);
        assertTrue(first.hasMore()); assertTrue(second.hasMore()); assertFalse(last.hasMore());
        assertEquals(3, last.items().size());
        assertEquals(103, queries.countGroups());
        assertEquals(103L, queries.pageGroups(1, 20, null).records().get(0).getId());
        assertThrows(ApiException.class, queries::legacyGroups);
    }

    @Test void indexMigrationIsRepeatableOnNewAndExistingSchema() {
        jdbc.execute("ALTER TABLE sys_user DROP INDEX idx_user_status_created");
        var migration = new ResourceDatabasePopulator(new ClassPathResource("db/migrations/V20260922_01__admin_query_indexes.sql"));
        migration.execute(dataSource);
        migration.execute(dataSource);
        List<String> names = jdbc.queryForList("SELECT DISTINCT index_name FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='sys_user'", String.class);
        assertTrue(names.contains("idx_user_status_created"));
        assertTrue(names.contains("idx_user_group_status_created"));
    }
}
