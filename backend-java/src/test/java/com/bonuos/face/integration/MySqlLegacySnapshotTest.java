package com.bonuos.face.integration;

import com.bonuos.face.service.LegacySnapshotService;
import com.bonuos.face.service.UserMutationService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.transaction.PlatformTransactionManager;
import javax.sql.DataSource;
import java.nio.file.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.MOCK)
@ActiveProfiles("test")
@EnabledIfEnvironmentVariable(named="FACE_TEST_DB_URL",matches="jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/face_test.*")
class MySqlLegacySnapshotTest {
    @Autowired DataSource dataSource;
    @Autowired JdbcTemplate jdbc;
    @Autowired ObjectMapper json;
    @Autowired PlatformTransactionManager transactions;
    @Autowired UserMutationService mutations;
    @TempDir Path directory;
    @BeforeEach void syntheticFixture(){
        assertEquals("face_test",jdbc.queryForObject("SELECT DATABASE()",String.class));
        new ResourceDatabasePopulator(new ClassPathResource("db/schema.sql")).execute(dataSource);
        jdbc.update("DELETE FROM sys_user");jdbc.update("DELETE FROM user_change_log");
        jdbc.update("DELETE FROM sync_lease");jdbc.update("DELETE FROM sync_device_cursor");
        jdbc.update("UPDATE sync_revision SET epoch=UUID(),head=0,retention_floor=0 WHERE id=1");
        for(int id=1;id<=501;id++)jdbc.update("INSERT INTO sys_user(id,user_no,name,status,feature_data,sync_version) VALUES(?,?,?,1,'[1,0]',0)",id,"legacy-"+id,"before-"+id);
    }
    @Test void allBoundedChunksShareTheClockSnapshotsConsistentRead()throws Exception{
        JdbcTemplate observed=spy(jdbc);AtomicInteger chunks=new AtomicInteger();
        doAnswer(invocation->{
            Object result=invocation.callRealMethod();
            if(chunks.incrementAndGet()==1){
                CompletableFuture.runAsync(()->mutations.update(400,"after-400",null,null,null)).get(5,TimeUnit.SECONDS);
            }
            return result;
        }).when(observed).execute(any(ConnectionCallback.class));
        LegacySnapshotService snapshots=new LegacySnapshotService(observed,json,transactions,directory.toString(),5_000_000);
        try(var lease=snapshots.acquire()){
            var document=json.readTree(Files.readString(lease.file()));
            assertEquals(0,lease.revision());assertEquals(501,document.get("data").size());
            assertEquals("before-400",document.get("data").get(399).get("name").asText());
            assertEquals(0,document.get("data").get(399).get("syncVersion").asLong());
        }finally{snapshots.closeCleanupScan();}
        assertEquals(3,chunks.get());
        assertEquals("after-400",jdbc.queryForObject("SELECT name FROM sys_user WHERE id=400",String.class));
        assertEquals(1,jdbc.queryForObject("SELECT head FROM sync_revision WHERE id=1",Long.class));
    }
}
