package com.bonuos.face.integration;

import com.bonuos.face.dto.SyncV2Dtos.*;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.security.DevicePrincipal;
import com.bonuos.face.service.SyncV2Service;
import com.bonuos.face.service.UserMutationService;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;
import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.MOCK)
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test.*")
class MySqlSyncV2Test {
    @Autowired DataSource dataSource;
    @Autowired JdbcTemplate jdbc;
    @Autowired SyncV2Service sync;
    @Autowired UserMutationService mutations;
    @Autowired PlatformTransactionManager transactions;
    @BeforeEach void fixture() {
        assertEquals("face_test",jdbc.queryForObject("SELECT DATABASE()",String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql"),
                new ClassPathResource("db/migrations/V20260924_03__sync_v2_progress.sql")).execute(dataSource);
        jdbc.update("DELETE FROM sys_user"); jdbc.update("DELETE FROM sys_user_group");
        jdbc.update("DELETE FROM user_change_log"); jdbc.update("DELETE FROM sync_lease"); jdbc.update("DELETE FROM sync_device_cursor");
        jdbc.update("UPDATE sync_revision SET epoch=UUID(),head=0,retention_floor=0 WHERE id=1");
        jdbc.update("INSERT INTO sys_user_group(id,group_no,group_name) VALUES(1,'sync-a','同步测试A'),(2,'sync-b','同步测试B')");
        identify("device-one",Set.of(1L),false,1);
    }
    @AfterEach void logout() { SecurityContextHolder.clearContext(); }
    private void identify(String sn,Set<Long> groups,boolean all,long scope) {
        var principal=new DevicePrincipal(sn,groups,all,scope,false);
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(principal,null,List.of()));
    }
    private User insert(String name,Long group) {
        User user=new User(); user.setUserNo(UUID.randomUUID().toString()); user.setName(name); user.setGroupId(group);
        user.setStatus(1); user.setFeatureData("[1,0]"); user.setCreateTime(LocalDateTime.now()); user.setUpdateTime(LocalDateTime.now());
        return mutations.insert(user);
    }
    @Test void snapshotAndCatchupConvergeAcrossMoveDeleteAndPostStartInsert() {
        User moved=insert("移动",1L), deleted=insert("删除",1L); insert("范围外",2L);
        Bootstrap boot=sync.bootstrap();
        var first=sync.page(boot.leaseId(),0,1);
        assertEquals(List.of(moved.getId()),first.records().stream().map(User::getId).toList()); assertTrue(first.hasMore());
        mutations.update(moved.getId(),"移出",2L,null,null);
        mutations.delete(deleted.getId()); User added=insert("后新增",1L);
        var last=sync.page(boot.leaseId(),first.nextAfterId(),1);
        assertFalse(last.hasMore()); assertTrue(last.records().isEmpty()); assertEquals(boot.maxId(),last.nextAfterId());
        var upper=sync.finish(boot.leaseId());
        List<Event> events=new ArrayList<>(); long cursor=boot.startRevision();
        while (cursor<upper.upperRevision()) {
            var changes=sync.changes(boot.epoch(),boot.scopeVersion(),cursor,upper.upperRevision(),1,boot.leaseId());
            assertTrue(changes.nextRevision()>cursor); cursor=changes.nextRevision(); events.addAll(changes.events());
        }
        assertEquals(List.of("delete","delete","upsert"),events.stream().map(Event::operation).toList());
        assertEquals(added.getId(),events.get(2).user().getId());
        assertEquals(upper.upperRevision(),sync.ack(new AckRequest(boot.epoch(),1L,cursor,boot.leaseId())).ackRevision());
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM sync_lease",Integer.class));
        assertEquals(cursor,sync.ack(new AckRequest(boot.epoch(),1L,cursor,boot.leaseId())).ackRevision());
    }
    @Test void filteredEventsStillAdvanceTheGlobalCursorAndAckCannotRegress() {
        insert("A",2L); insert("B",2L); User visible=insert("C",1L);
        State state=sync.state(); var first=sync.changes(state.epoch(),1,0,state.head(),1,null);
        assertTrue(first.events().isEmpty()); assertEquals(1,first.nextRevision()); assertTrue(first.hasMore());
        var last=sync.changes(state.epoch(),1,first.nextRevision(),state.head(),200,null);
        assertEquals(visible.getId(),last.events().get(0).userId()); assertEquals(state.head(),last.nextRevision());
        assertEquals(3,sync.ack(new AckRequest(state.epoch(),1L,3L,null)).ackRevision());
        assertEquals(3,sync.ack(new AckRequest(state.epoch(),1L,1L,null)).ackRevision());
    }
    @Test void retriesDoNotRegressScanProgressAndFinishKeepsItsOriginalUpperBound() {
        insert("A",1L); insert("B",1L); var boot=sync.bootstrap();
        assertEquals("SNAPSHOT_INCOMPLETE",assertThrows(ApiException.class,()->sync.finish(boot.leaseId())).getErrorCode());
        assertThrows(ApiException.class,()->sync.page(boot.leaseId(),boot.maxId(),1));
        var first=sync.page(boot.leaseId(),0,1);
        assertEquals(first.nextAfterId(),sync.page(boot.leaseId(),0,1).nextAfterId());
        sync.page(boot.leaseId(),first.nextAfterId(),1); var upper=sync.finish(boot.leaseId());
        insert("C",1L);
        assertEquals(upper.upperRevision(),sync.finish(boot.leaseId()).upperRevision());
        assertThrows(ApiException.class,()->sync.page(boot.leaseId(),0,1));
        assertThrows(ApiException.class,()->sync.changes(boot.epoch(),1,0,upper.upperRevision(),200,boot.leaseId()));
    }
    @Test void scopeEpochExpiryAndRetentionRequireRebootstrap() {
        insert("A",1L); var boot=sync.bootstrap();
        identify("device-one",Set.of(2L),false,2);
        assertSnapshot(()->sync.page(boot.leaseId(),0,200));
        identify("device-other",Set.of(1L),false,1);
        assertSnapshot(()->sync.page(boot.leaseId(),0,200));
        identify("device-one",Set.of(1L),false,1);
        jdbc.update("UPDATE sync_lease SET expires_at=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3)) WHERE id=?",boot.leaseId());
        assertSnapshot(()->sync.page(boot.leaseId(),0,200));
        jdbc.update("UPDATE sync_revision SET retention_floor=1 WHERE id=1");
        assertSnapshot(()->sync.changes(boot.epoch(),1,0,1,200,null));
        jdbc.update("UPDATE sync_revision SET epoch=UUID() WHERE id=1");
        assertSnapshot(()->sync.changes(boot.epoch(),1,1,1,200,null));
    }
    @Test void newBootstrapInvalidatesTheOldLeaseAndKeepsOnlyOnePerDevice() {
        var old=sync.bootstrap(); var current=sync.bootstrap();
        assertNotEquals(old.leaseId(),current.leaseId());
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM sync_lease",Integer.class));
        assertSnapshot(()->sync.page(old.leaseId(),0,200));
        assertFalse(sync.page(current.leaseId(),0,200).hasMore());
        assertEquals(0,sync.finish(current.leaseId()).upperRevision());
    }
    @Test void unexpectedMissingEventCannotBecomeSuccessfulEmptyIncrement() {
        insert("A",1L); insert("B",1L); State state=sync.state();
        jdbc.update("DELETE FROM user_change_log WHERE revision=1");
        ApiException error=assertThrows(ApiException.class,()->sync.changes(state.epoch(),1,0,2,200,null));
        assertEquals("SYNC_DATA_INVALID",error.getErrorCode()); assertEquals(503,error.getStatus().value());
    }
    @Test void repeatableReadReturnsCompleteOldIntervalDuringConcurrentRetentionCommit() {
        insert("A",1L); insert("B",1L);
        var transaction=new TransactionTemplate(transactions);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);
        transaction.executeWithoutResult(status -> {
            State snapshot=sync.state();
            try (Connection other=dataSource.getConnection(); Statement sql=other.createStatement()) {
                other.setAutoCommit(false);
                sql.executeQuery("SELECT head FROM sync_revision WHERE id=1 FOR UPDATE").close();
                sql.executeUpdate("DELETE FROM user_change_log WHERE revision<=2");
                sql.executeUpdate("UPDATE sync_revision SET retention_floor=2 WHERE id=1");
                other.commit();
            } catch (Exception error) { throw new IllegalStateException(error); }
            var changes=sync.changes(snapshot.epoch(),1,0,2,200,null);
            assertEquals(2,changes.events().size()); assertEquals(2,changes.nextRevision());
        });
        State fresh=sync.state(); assertSnapshot(()->sync.changes(fresh.epoch(),1,0,2,200,null));
    }
    private void assertSnapshot(Runnable operation) {
        ApiException error=assertThrows(ApiException.class,operation::run);
        assertEquals("SNAPSHOT_REQUIRED",error.getErrorCode()); assertEquals(409,error.getStatus().value());
    }
}
