package com.bonuos.face.service;

import com.bonuos.face.dto.SyncV2Dtos.AckRequest;
import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.bonuos.face.security.DevicePrincipal;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import java.util.List;
import java.util.Set;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class SyncV2ServiceTest {
    private final JdbcTemplate jdbc=mock(JdbcTemplate.class);
    private final ObjectMapper json=new ObjectMapper();
    private final SyncV2Service sync=new SyncV2Service(jdbc,json,3600);
    private final DevicePrincipal groupOne=new DevicePrincipal("test-device",Set.of(1L),false,7,false);
    @AfterEach void clearIdentity() { SecurityContextHolder.clearContext(); }
    private String image(long id,long revision,Long group,int status) throws Exception {
        User user=new User(); user.setId(id); user.setSyncVersion(revision); user.setGroupId(group);
        user.setStatus(status); user.setFeatureData("[1,0]"); user.setName("synthetic");
        return json.writeValueAsString(user);
    }
    @Test void movingOutOfAnAuthorizedGroupBecomesDeletionWithoutDisclosingTheNewPostimage() {
        var event=sync.project(groupOne,new SyncV2Service.ChangeRow(8,9,1L,2L,"upsert","not decoded outside scope"));
        assertEquals("delete",event.operation()); assertEquals(9,event.userId()); assertNull(event.user());
    }
    @Test void movingInPublishesCompleteNewRecord() throws Exception {
        var event=sync.project(groupOne,new SyncV2Service.ChangeRow(8,9,2L,1L,"upsert",image(9,8,1L,1)));
        assertEquals("upsert",event.operation()); assertEquals("[1,0]",event.user().getFeatureData());
        assertEquals(8L,event.user().getSyncVersion());
    }
    @Test void outOfScopeChangesAreFilteredWithoutParsingTheirFeatures() {
        assertNull(sync.project(groupOne,new SyncV2Service.ChangeRow(8,9,2L,3L,"upsert","private")));
        assertNull(sync.project(groupOne,new SyncV2Service.ChangeRow(9,9,2L,null,"delete",null)));
    }
    @Test void disabledRecordProducesDeletionForItsPreviousScope() throws Exception {
        var event=sync.project(groupOne,new SyncV2Service.ChangeRow(8,9,1L,1L,"upsert",image(9,8,1L,0)));
        assertEquals("delete",event.operation());
    }
    @Test void allGroupsIncludesUngroupedRecords() throws Exception {
        var all=new DevicePrincipal("all",Set.of(),true,1,false);
        assertEquals("upsert",sync.project(all,new SyncV2Service.ChangeRow(8,9,null,null,"upsert",image(9,8,null,1))).operation());
        assertEquals("delete",sync.project(all,new SyncV2Service.ChangeRow(9,9,null,null,"delete",null)).operation());
    }
    @Test void malformedOrMismatchedPostimagesFailWithoutSuccessfulEmptyResult() throws Exception {
        for (String bad:List.of("malformed",image(9,7,1L,1),image(99,8,1L,1),image(9,8,2L,1))) {
            ApiException error=assertThrows(ApiException.class,()->sync.project(groupOne,new SyncV2Service.ChangeRow(8,9,1L,1L,"upsert",bad)));
            assertEquals("SYNC_DATA_INVALID",error.getErrorCode()); assertEquals(503,error.getStatus().value());
        }
    }
    @Test void invalidPageBoundsNeverAccessDatabase() {
        assertThrows(ApiException.class,()->sync.page("x",0,201));
        assertThrows(ApiException.class,()->sync.page("x",-1,20));
        assertThrows(ApiException.class,()->sync.changes("epoch",1,10,9,20,null));
        assertThrows(ApiException.class,()->sync.ack(new AckRequest("epoch",1L,-1L,null)));
        verifyNoInteractions(jdbc);
    }
    @Test void legacyIdentityCannotUseV2() {
        var legacy=new DevicePrincipal("legacy",Set.of(),true,0,true);
        SecurityContextHolder.getContext().setAuthentication(new UsernamePasswordAuthenticationToken(legacy,null,List.of()));
        ApiException error=assertThrows(ApiException.class,sync::state);
        assertEquals(403,error.getStatus().value()); verifyNoInteractions(jdbc);
    }
}
