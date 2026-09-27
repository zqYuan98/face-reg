package com.bonuos.face.integration;

import com.bonuos.face.entity.*;
import com.bonuos.face.service.*;
import com.bonuos.face.exception.ApiException;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.core.io.ClassPathResource;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import javax.sql.DataSource;
import java.time.LocalDateTime;
import java.util.UUID;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest @ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test(?:\\?.*)?")
class MySqlMutationTest {
    @Autowired JdbcTemplate jdbc; @Autowired DataSource dataSource;
    @Autowired UserMutationService mutations; @Autowired PlatformTransactionManager transactions;
    @Autowired DeviceService devices;
    @BeforeEach void isolatedFixture() {
        assertEquals("face_test",jdbc.queryForObject("SELECT DATABASE()",String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql"),new ClassPathResource("db/migrations/V20260922_02__sync_revision.sql")).execute(dataSource);
        jdbc.update("DELETE FROM sys_user"); jdbc.update("DELETE FROM user_change_log"); jdbc.update("DELETE FROM sync_lease");
        jdbc.update("UPDATE sync_revision SET head=0,retention_floor=0,epoch=UUID() WHERE id=1");
    }
    private User draft(String name) {
        User u=new User(); u.setUserNo(UUID.randomUUID().toString()); u.setName(name); u.setStatus(1);
        u.setFeatureData("[0.1,0.2]");u.setPhotoUrl("test.jpg");u.setCreateTime(LocalDateTime.now());u.setUpdateTime(LocalDateTime.now());return u;
    }
    @Test void rollbackDoesNotPublishARevisionOrLeaveAPerson() {
        new TransactionTemplate(transactions).execute(status->{ mutations.insert(draft("rollback")); status.setRollbackOnly(); return null; });
        assertEquals(0L,jdbc.queryForObject("SELECT head FROM sync_revision WHERE id=1",Long.class));
        assertEquals(0L,jdbc.queryForObject("SELECT COUNT(*) FROM sys_user",Long.class));
        assertEquals(0L,jdbc.queryForObject("SELECT COUNT(*) FROM user_change_log",Long.class));
    }
    @Test void concurrentWriterCannotPublishPastAnUncommittedRevision() throws Exception {
        ExecutorService pool=Executors.newFixedThreadPool(2); CountDownLatch firstWritten=new CountDownLatch(1),commitFirst=new CountDownLatch(1);
        try {
            Future<?> first=pool.submit(()->new TransactionTemplate(transactions).execute(status->{
                mutations.insert(draft("first"));firstWritten.countDown();
                try { if(!commitFirst.await(3,TimeUnit.SECONDS))throw new IllegalStateException("test barrier timed out"); } catch(InterruptedException e){throw new IllegalStateException(e);}return null;
            }));
            assertTrue(firstWritten.await(2,TimeUnit.SECONDS));
            Future<User> second=pool.submit(()->mutations.insert(draft("second")));
            assertThrows(TimeoutException.class,()->second.get(100,TimeUnit.MILLISECONDS));
            assertEquals(0L,jdbc.queryForObject("SELECT head FROM sync_revision WHERE id=1",Long.class));
            commitFirst.countDown();first.get(3,TimeUnit.SECONDS); assertEquals(2L,second.get(3,TimeUnit.SECONDS).getSyncVersion());
            assertEquals(java.util.List.of(1L,2L),jdbc.queryForList("SELECT revision FROM user_change_log ORDER BY revision",Long.class));
        } finally { commitFirst.countDown();pool.shutdownNow(); }
    }
    @Test void nullGroupNamesConflictAndDeleteHasTombstone() {
        User first=mutations.insert(draft("same"));
        assertEquals("DUPLICATE_NAME",assertThrows(ApiException.class,()->mutations.insert(draft("same"))).getErrorCode());
        assertTrue(mutations.delete(first.getId()).deleted());
        assertEquals("delete",jdbc.queryForObject("SELECT operation FROM user_change_log WHERE revision=2",String.class));
        assertFalse(mutations.delete(first.getId()).deleted());
        assertEquals(2L,jdbc.queryForObject("SELECT head FROM sync_revision WHERE id=1",Long.class));
    }
    @Test void uniqueMigrationCanRepeatAndGuardsDirectWriters() {
        var migration=new ResourceDatabasePopulator(new ClassPathResource("db/migrations/V20260922_04__active_name_unique.sql"));
        migration.execute(dataSource);migration.execute(dataSource);
        mutations.insert(draft("guarded"));
        assertThrows(org.springframework.dao.DuplicateKeyException.class,()->jdbc.update("INSERT INTO sys_user(user_no,name,status) VALUES(?,?,1)",UUID.randomUUID().toString(),"guarded"));
    }
    @Test void repeatedDeviceRegistrationUsesSameRow() {
        String sn="test-"+UUID.randomUUID(); long first=devices.registerOrHeartbeat(sn,"127.0.0.1").getId();
        assertEquals(first,devices.registerOrHeartbeat(sn,"127.0.0.2").getId());
        jdbc.update("DELETE FROM sys_device WHERE device_sn=?",sn);
    }
}
