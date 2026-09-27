package com.bonuos.face.integration;

import com.bonuos.face.security.DevicePrincipal;
import com.bonuos.face.security.PhotoAccess;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import javax.sql.DataSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.test.context.ActiveProfiles;
import static org.junit.jupiter.api.Assertions.*;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.MOCK)
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test.*")
class MySqlPhotoAccessTest {
    @Autowired DataSource dataSource;
    @Autowired JdbcTemplate jdbc;
    @Autowired PhotoAccess photos;
    private String epoch;

    @BeforeEach void fixture() {
        assertEquals("face_test", jdbc.queryForObject("SELECT DATABASE()", String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql"),
                new ClassPathResource("db/migrations/V20260924_07__retained_photos.sql"),
                new ClassPathResource("db/migrations/V20260924_08__before_photo_and_cursor_index.sql")).execute(dataSource);
        jdbc.update("DELETE FROM sys_user");
        jdbc.update("DELETE FROM sys_user_group");
        jdbc.update("DELETE FROM user_change_log");
        jdbc.update("DELETE FROM sync_lease");
        jdbc.update("DELETE FROM sync_device_cursor");
        epoch=UUID.randomUUID().toString();
        jdbc.update("UPDATE sync_revision SET epoch=?,head=100,retention_floor=0 WHERE id=1",epoch);
        jdbc.update("INSERT INTO sys_user_group(id,group_no,group_name) VALUES(7,'photo-a','照片组A'),(9,'photo-b','照片组B')");
    }

    @Test void currentUnicodeAndLiteralPlusPhotosNeedNoHistoryLeaseButMustMatchGroup() {
        jdbc.update("INSERT INTO sys_user(user_no,name,group_id,photo_url,status) VALUES('photo-user','照片人员',7,?,1)",
                "uuid_中文 空格+a.jpg");
        assertTrue(allowed("/uploads/uuid_%E4%B8%AD%E6%96%87%20%E7%A9%BA%E6%A0%BC+a.jpg", "scoped", 7, 2));
        assertFalse(allowed("/uploads/uuid_%E4%B8%AD%E6%96%87%20%E7%A9%BA%E6%A0%BC+a.jpg", "scoped", 9, 2));
    }

    @Test void oldAndNewPhotoGroupsArePairedAndUninitializedDeviceCannotReadHistory() {
        event(11,"old.jpg",7L,"new.jpg",9L);
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
        lease("scoped",2,10);
        assertTrue(allowed("/uploads/old.jpg","scoped",7,2));
        assertFalse(allowed("/uploads/new.jpg","scoped",7,2));
        lease("other",1,10);
        assertTrue(allowed("/uploads/new.jpg","other",9,1));
        assertFalse(allowed("/uploads/old.jpg","other",9,1));
    }

    @Test void caseAndAccentInsensitiveDatabaseCollationCannotAuthorizeAnotherFilename() {
        jdbc.update("INSERT INTO sys_user(user_no,name,group_id,photo_url,status) VALUES('exact-photo','精确文件名',7,'uuid_é.jpg',1)");
        assertTrue(allowed("/uploads/uuid_%C3%A9.jpg","scoped",7,2));
        assertFalse(allowed("/uploads/UUID_%C3%A9.jpg","scoped",7,2));
        assertFalse(allowed("/uploads/uuid_e.jpg","scoped",7,2));
        event(11,"old_é.jpg",7L,"new.jpg",9L);
        lease("scoped",2,10);
        assertTrue(allowed("/uploads/old_%C3%A9.jpg","scoped",7,2));
        assertFalse(allowed("/uploads/old_e.jpg","scoped",7,2));
    }

    @Test void acknowledgedMoveOrReplaceRevokesOldPhotoAccess() {
        event(11,"old.jpg",7L,"new.jpg",9L);
        ack("scoped",2,10);
        assertTrue(allowed("/uploads/old.jpg","scoped",7,2));
        jdbc.update("UPDATE sync_device_cursor SET ack_revision=11 WHERE device_sn='scoped'");
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
        jdbc.update("INSERT INTO sys_user(user_no,name,group_id,photo_url,status) VALUES('current','当前照片',7,'current.jpg',1)");
        assertTrue(allowed("/uploads/current.jpg","scoped",7,2));
    }

    @Test void expiredLeaseWrongEpochAndChangedScopeDoNotGrantOldPhoto() {
        event(11,"old.jpg",7L,"new.jpg",9L);
        lease("scoped",2,10);
        assertTrue(allowed("/uploads/old.jpg","scoped",7,2));
        assertFalse(allowed("/uploads/old.jpg","scoped",7,3));
        assertFalse(allowed("/uploads/old.jpg","different-device",7,2));
        jdbc.update("UPDATE sync_lease SET expires_at=TIMESTAMPADD(SECOND,-1,CURRENT_TIMESTAMP(3))");
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
        jdbc.update("UPDATE sync_lease SET expires_at=TIMESTAMPADD(HOUR,1,CURRENT_TIMESTAMP(3)),epoch=UUID()");
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
    }

    @Test void staleAckWrongEpochScopeAndRetentionFloorFailClosed() {
        event(11,"old.jpg",7L,"new.jpg",9L);
        ack("scoped",2,10);
        assertTrue(allowed("/uploads/old.jpg","scoped",7,2));
        assertFalse(allowed("/uploads/old.jpg","scoped",7,3));
        jdbc.update("UPDATE sync_device_cursor SET updated_at=TIMESTAMPADD(DAY,-8,CURRENT_TIMESTAMP(3))");
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
        jdbc.update("UPDATE sync_device_cursor SET updated_at=CURRENT_TIMESTAMP(3),epoch=UUID()");
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
        jdbc.update("UPDATE sync_device_cursor SET epoch=?",epoch);
        jdbc.update("UPDATE sync_revision SET retention_floor=11 WHERE id=1");
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
    }

    @Test void baselineZeroPhotoIsRetainedByTheLaterReplacementEvent() {
        event(1,"baseline.jpg",7L,"replacement.jpg",7L);
        lease("scoped",2,0);
        assertTrue(allowed("/uploads/baseline.jpg","scoped",7,2));
        jdbc.update("UPDATE sync_lease SET start_revision=1");
        assertFalse(allowed("/uploads/baseline.jpg","scoped",7,2));
    }

    @Test void eventsPastPublishedHeadCannotAuthorizeDownloads() {
        event(11,"old.jpg",7L,"new.jpg",7L);
        lease("scoped",2,0);
        jdbc.update("UPDATE sync_revision SET head=10 WHERE id=1");
        assertFalse(allowed("/uploads/old.jpg","scoped",7,2));
        jdbc.update("UPDATE sync_revision SET head=11 WHERE id=1");
        assertTrue(allowed("/uploads/old.jpg","scoped",7,2));
    }

    private void event(long revision,String oldPhoto,Long oldGroup,String newPhoto,Long newGroup) {
        jdbc.update("INSERT INTO user_change_log(revision,user_id,old_group_id,new_group_id,operation,postimage,photo_url,old_photo_url) "
                + "VALUES(?,1,?,?,'upsert','{}',?,?)",revision,oldGroup,newGroup,newPhoto,oldPhoto);
    }
    private void lease(String device,long scope,long start) {
        jdbc.update("INSERT INTO sync_lease(id,device_sn,scope_version,epoch,start_revision,max_id,expires_at) "
                + "VALUES(?,?,?,?,?,100,TIMESTAMPADD(HOUR,1,CURRENT_TIMESTAMP(3)))",UUID.randomUUID().toString(),device,scope,epoch,start);
    }
    private void ack(String device,long scope,long revision) {
        jdbc.update("INSERT INTO sync_device_cursor(device_sn,scope_version,epoch,ack_revision,updated_at) "
                + "VALUES(?,?,?,?,CURRENT_TIMESTAMP(3))",device,scope,epoch,revision);
    }
    private boolean allowed(String uri,String device,long group,long scope) {
        var request=new MockHttpServletRequest(); request.setRequestURI(uri);
        var principal=new DevicePrincipal(device, Set.of(group),false,scope,false);
        return photos.allowed(UsernamePasswordAuthenticationToken.authenticated(principal,null, List.of()),request);
    }
}
