package com.bonuos.face.integration;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.QueryTimeoutException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import static org.junit.jupiter.api.Assertions.*;

/** Confirms the real driver cancels a slow statement and the pool remains usable. */
@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.MOCK,
        properties="face.sync.retention-interval-ms=3600000")
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test.*")
class MySqlTimeoutTest {
    @Autowired JdbcTemplate jdbc;
    @Test void configuredTimeoutCancelsTheServerQueryAndReturnsItsConnection() {
        assertEquals("face_test",jdbc.queryForObject("SELECT DATABASE()",String.class));
        long started=System.nanoTime();
        assertThrows(QueryTimeoutException.class,()->jdbc.queryForObject("SELECT SLEEP(20)",Integer.class));
        assertTrue((System.nanoTime()-started)/1_000_000_000.0<15,"20-second query must be cancelled before natural completion");
        assertEquals(1,jdbc.queryForObject("SELECT 1",Integer.class));
    }
}
