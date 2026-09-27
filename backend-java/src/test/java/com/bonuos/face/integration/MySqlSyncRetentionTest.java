package com.bonuos.face.integration;

import com.bonuos.face.service.SyncRetentionService;
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
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;

/** Opt-in synthetic MySQL fixtures; never execute against a business database. */
@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.MOCK,properties={
        "face.sync.retention-days=7", "face.sync.retention-interval-ms=3600000"
})
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test.*")
class MySqlSyncRetentionTest {
    @Autowired DataSource dataSource;
    @Autowired JdbcTemplate jdbc;
    @Autowired SyncRetentionService retention;
    private String epoch;

    @BeforeEach void syntheticFixture(){
        // Validate the actual selected schema before executing any fixture mutation.
        assertEquals("face_test",jdbc.queryForObject("SELECT DATABASE()",String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql")).execute(dataSource);
        jdbc.update("DELETE FROM user_change_log");
        jdbc.update("DELETE FROM sync_lease");
        jdbc.update("DELETE FROM sync_device_cursor");
        epoch=UUID.randomUUID().toString();
        jdbc.update("UPDATE sync_revision SET epoch=?,head=0,retention_floor=0 WHERE id=1",epoch);
    }

    private void oldEvents(int count){
        List<Object[]> rows=new ArrayList<>();
        for(long revision=1;revision<=count;revision++)rows.add(new Object[]{revision,revision});
        jdbc.batchUpdate("INSERT INTO user_change_log(revision,user_id,operation,created_at) "
                +"VALUES(?,?,'delete',TIMESTAMPADD(DAY,-30,NOW(3)))",rows);
        jdbc.update("UPDATE sync_revision SET head=? WHERE id=1",count);
    }
    private String lease(String device,String leaseEpoch,long start,boolean live){
        String id=UUID.randomUUID().toString();
        jdbc.update("INSERT INTO sync_lease(id,device_sn,scope_version,epoch,start_revision,max_id,expires_at) "
                        +"VALUES(?,?,1,?,?,0,TIMESTAMPADD(SECOND,?,NOW(3)))",
                id,device,leaseEpoch,start,live?3600:-3600);
        return id;
    }
    private void ack(String device,String ackEpoch,long revision,boolean recent){
        jdbc.update("INSERT INTO sync_device_cursor(device_sn,scope_version,epoch,ack_revision,updated_at) "
                        +"VALUES(?,1,?,?,TIMESTAMPADD(DAY,?,NOW(3)))",
                device,ackEpoch,revision,recent?0:-30);
    }
    private long floor(){return jdbc.queryForObject("SELECT retention_floor FROM sync_revision WHERE id=1",Long.class);}
    private List<Long> revisions(){return jdbc.queryForList("SELECT revision FROM user_change_log ORDER BY revision",Long.class);}

    @Test void activeLeasePreservesEveryEventAfterItsBootstrapStart(){
        oldEvents(8);
        String active=lease("boot-current",epoch,3,true);
        lease("boot-expired",epoch,0,false);
        retention.prune();
        assertEquals(3,floor());
        assertEquals(List.of(4L,5L,6L,7L,8L),revisions());
        assertEquals(1,jdbc.queryForObject("SELECT COUNT(*) FROM sync_lease",Integer.class));
        retention.prune();
        assertEquals(3,floor(),"repeated pruning cannot advance across a live bootstrap lease");
        jdbc.update("DELETE FROM sync_lease WHERE id=?",active);
        retention.prune();
        assertEquals(8,floor());assertTrue(revisions().isEmpty());
    }

    @Test void recentAcknowledgementsProtectTheSlowestActiveDeviceAndOldOnesExpire(){
        oldEvents(8);
        ack("slow-current",epoch,2,true);
        ack("fast-current",epoch,6,true);
        ack("offline-expired",epoch,0,false);
        retention.prune();
        assertEquals(2,floor());
        assertEquals(List.of(3L,4L,5L,6L,7L,8L),revisions());
        assertEquals(0,jdbc.queryForObject("SELECT COUNT(*) FROM sync_device_cursor WHERE device_sn='offline-expired'",Integer.class));
        jdbc.update("UPDATE sync_device_cursor SET ack_revision=6,updated_at=NOW(3) WHERE device_sn='slow-current'");
        retention.prune();
        assertEquals(6,floor());assertEquals(List.of(7L,8L),revisions());
        jdbc.update("UPDATE sync_device_cursor SET ack_revision=8,updated_at=NOW(3)");
        retention.prune();assertEquals(8,floor());assertTrue(revisions().isEmpty());
    }

    @Test void aRecentEventStopsPruningEvenWhenLaterEventsHaveOlderTimestamps(){
        oldEvents(5);
        jdbc.update("UPDATE user_change_log SET created_at=NOW(3) WHERE revision=3");
        retention.prune();
        assertEquals(2,floor());assertEquals(List.of(3L,4L,5L),revisions());
        retention.prune();assertEquals(2,floor());
        jdbc.update("UPDATE user_change_log SET created_at=TIMESTAMPADD(DAY,-30,NOW(3)) WHERE revision=3");
        retention.prune();assertEquals(5,floor());assertTrue(revisions().isEmpty());
    }

    @Test void aMissingRevisionCannotBeCrossedByTheRetentionFloor(){
        oldEvents(5);
        jdbc.update("DELETE FROM user_change_log WHERE revision=3");
        retention.prune();
        assertEquals(2,floor());assertEquals(List.of(4L,5L),revisions());
        retention.prune();assertEquals(2,floor());assertEquals(List.of(4L,5L),revisions());
    }

    @Test void eachInvocationDeletesAtMostOneThousandContiguousEvents(){
        oldEvents(2505);
        retention.prune();
        assertEquals(1000,floor());assertEquals(1505,revisions().size());
        assertEquals(1001L,revisions().get(0));
        retention.prune();
        assertEquals(2000,floor());assertEquals(505,revisions().size());
        assertEquals(2001L,revisions().get(0));
        retention.prune();
        assertEquals(2505,floor());assertTrue(revisions().isEmpty());
        retention.prune();assertEquals(2505,floor());
    }

    @Test void previousEpochLeasesAndAcknowledgementsDoNotPinCurrentHistory(){
        oldEvents(8);
        String oldEpoch=UUID.randomUUID().toString();
        lease("old-bootstrap",oldEpoch,0,true);
        ack("old-device",oldEpoch,0,true);
        String current=lease("current-bootstrap",epoch,5,true);
        retention.prune();
        assertEquals(5,floor());assertEquals(List.of(6L,7L,8L),revisions());
        assertEquals(epoch,jdbc.queryForObject("SELECT epoch FROM sync_revision WHERE id=1",String.class));
        jdbc.update("DELETE FROM sync_lease WHERE id=?",current);
        retention.prune();assertEquals(8,floor());assertTrue(revisions().isEmpty());
    }
}
