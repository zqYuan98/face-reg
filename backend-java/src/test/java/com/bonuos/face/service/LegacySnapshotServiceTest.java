package com.bonuos.face.service;

import com.bonuos.face.exception.ApiException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.SimpleTransactionStatus;
import java.io.IOException;
import java.nio.file.*;
import java.nio.file.attribute.FileTime;
import java.sql.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LegacySnapshotServiceTest {
    @TempDir Path directory;
    private final List<LegacySnapshotService> services=new ArrayList<>();
    private final ObjectMapper json=new ObjectMapper();
    @AfterEach void closeScans(){services.forEach(LegacySnapshotService::closeCleanupScan);}
    private final class Fixture {
        final JdbcTemplate jdbc=mock(JdbcTemplate.class);
        final PlatformTransactionManager manager=mock(PlatformTransactionManager.class);
        final List<PreparedStatement> statements=new ArrayList<>();
        long head=0; int people=0; String feature="[1,0]"; Runnable afterRead=()->{};
        Fixture() throws Exception {
            when(manager.getTransaction(any())).thenReturn(new SimpleTransactionStatus());
            when(jdbc.queryForMap(anyString())).thenAnswer(invocation->Map.of("epoch","test-epoch","head",head));
            doAnswer(invocation->{
                Connection connection=mock(Connection.class); PreparedStatement statement=mock(PreparedStatement.class);
                statements.add(statement); AtomicLong after=new AtomicLong(); AtomicInteger take=new AtomicInteger();
                when(connection.prepareStatement(anyString())).thenAnswer(sql->{
                    assertTrue(sql.<String>getArgument(0).contains("id>? ORDER BY id LIMIT ?")); return statement;
                });
                doAnswer(call->{after.set(call.getArgument(1));return null;}).when(statement).setLong(eq(1),anyLong());
                doAnswer(call->{take.set(call.getArgument(1));return null;}).when(statement).setInt(eq(2),anyInt());
                when(statement.executeQuery()).thenAnswer(call->{
                    assertEquals(200,take.get());
                    ResultSet rows=mock(ResultSet.class); AtomicLong id=new AtomicLong(after.get());
                    long end=Math.min(people,after.get()+take.get());
                    when(rows.next()).thenAnswer(next->id.incrementAndGet()<=end);
                    when(rows.getLong(anyInt())).thenAnswer(get->id.get());
                    when(rows.getString(anyInt())).thenAnswer(get->switch(get.<Integer>getArgument(0)){
                        case 2 -> "user-"+id.get(); case 3 -> "person-"+id.get(); case 5 -> feature; default -> null;
                    });
                    when(rows.getObject(4,Long.class)).thenReturn(1L);
                    when(rows.getInt(7)).thenReturn(1);
                    afterRead.run(); return rows;
                });
                @SuppressWarnings("unchecked") ConnectionCallback<Object> callback=invocation.getArgument(0);
                return callback.doInConnection(connection);
            }).when(jdbc).execute(any(ConnectionCallback.class));
        }
        LegacySnapshotService service(long maxBytes){
            LegacySnapshotService service=new LegacySnapshotService(jdbc,json,manager,directory.toString(),maxBytes);
            services.add(service); return service;
        }
    }
    private Path oldArtifact(String suffix)throws IOException{
        Path file=Files.writeString(directory.resolve("snapshot-"+UUID.randomUUID()+suffix),"generated fixture");
        makeOld(file);return file;
    }
    private void makeOld(Path file)throws IOException{Files.setLastModifiedTime(file,FileTime.fromMillis(System.currentTimeMillis()-7_200_000));}

    @Test void completeArrayUsesBoundedQueriesInOneRepeatableReadTransactionAndReusesDisk()throws Exception{
        Fixture fixture=new Fixture();fixture.people=501;fixture.head=501;
        LegacySnapshotService service=fixture.service(5_000_000);
        Path completed;
        try(var lease=service.acquire()){
            completed=lease.file();var data=json.readTree(Files.readString(completed));
            assertEquals(200,data.get("code").asInt());assertEquals(501,data.get("data").size());
            assertEquals(1,data.get("data").get(0).get("id").asLong());
            assertEquals(501,data.get("data").get(500).get("id").asLong());
        }
        assertEquals(3,fixture.statements.size());
        for(PreparedStatement statement:fixture.statements){verify(statement).setQueryTimeout(5);verify(statement,never()).setFetchSize(anyInt());}
        verify(fixture.statements.get(0)).setLong(1,0);
        verify(fixture.statements.get(1)).setLong(1,200);
        verify(fixture.statements.get(2)).setLong(1,400);
        verify(fixture.manager).getTransaction(argThat(definition->definition.getIsolationLevel()==TransactionDefinition.ISOLATION_REPEATABLE_READ
                &&definition.isReadOnly()&&definition.getTimeout()==60));
        try(var reused=service.acquire()){assertEquals(completed,reused.file());}
        assertEquals(3,fixture.statements.size());
    }

    @Test void byteLimitFailureNeverPublishesPartialArrayOrReplacesPreviousCompleteFile()throws Exception{
        Fixture fixture=new Fixture();LegacySnapshotService service=fixture.service(1000);
        Path complete;try(var first=service.acquire()){complete=first.file();}
        String original=Files.readString(complete);
        fixture.head=1;fixture.people=10;fixture.feature="x".repeat(2000);
        ApiException error=assertThrows(ApiException.class,service::acquire);
        assertEquals("SNAPSHOT_UNAVAILABLE",error.getErrorCode());
        assertEquals(original,Files.readString(complete));
        try(var files=Files.list(directory)){assertEquals(List.of(complete),files.toList());}
        // The rejected attempt released its download permit; old revision is still reusable.
        fixture.head=0;try(var lease=service.acquire()){assertEquals(complete,lease.file());}
    }

    @Test void totalDeadlineStopsBeforeAnotherChunkAndPreservesPreviousCompleteFile()throws Exception{
        Fixture fixture=new Fixture();LegacySnapshotService real=fixture.service(5_000_000);
        LegacySnapshotService service=spy(real);services.add(service);
        Path complete;try(var first=service.acquire()){complete=first.file();}
        AtomicLong time=new AtomicLong();doAnswer(call->time.get()).when(service).nowNanos();
        fixture.head=1;fixture.people=501;fixture.afterRead=()->time.set(60_000_000_000L);
        int before=fixture.statements.size();
        assertThrows(ApiException.class,service::acquire);
        assertEquals(before+1,fixture.statements.size(),"deadline must prevent a second bounded SQL query");
        assertEquals(0,json.readTree(Files.readString(complete)).get("data").size());
        try(var files=Files.list(directory)){assertEquals(List.of(complete),files.toList());}
    }

    @Test void restartCleanupIsBoundedAndIgnoresUnrelatedYoungAndDirectoryEntries()throws Exception{
        Fixture fixture=new Fixture();LegacySnapshotService service=fixture.service(5_000_000);
        List<Path> garbage=new ArrayList<>();for(int i=0;i<25;i++)garbage.add(oldArtifact(i%2==0?".json":".tmp"));
        Path oldNumeric=Files.writeString(directory.resolve("snapshot-1234567890123456789.tmp"),"old-format");makeOld(oldNumeric);garbage.add(oldNumeric);
        Path unrelated=Files.writeString(directory.resolve("snapshot-business-report.json"),"keep");makeOld(unrelated);
        Path young=Files.writeString(directory.resolve("snapshot-"+UUID.randomUUID()+".json"),"young");
        Path subdirectory=Files.createDirectory(directory.resolve("snapshot-"+UUID.randomUUID()+".tmp"));makeOld(subdirectory);
        service.cleanupGeneratedSnapshots();assertEquals(6,garbage.stream().filter(Files::exists).count());
        service.cleanupGeneratedSnapshots();assertEquals(0,garbage.stream().filter(Files::exists).count());
        assertTrue(Files.exists(unrelated));assertTrue(Files.exists(young));assertTrue(Files.isDirectory(subdirectory));
    }

    @Test void currentAndLeasedRetiredFilesRemainProtectedFromCleanup()throws Exception{
        Fixture fixture=new Fixture();LegacySnapshotService service=fixture.service(5_000_000);
        var old=service.acquire();makeOld(old.file());
        fixture.head=1;fixture.people=1;var current=service.acquire();makeOld(current.file());
        Path garbage=oldArtifact(".tmp");service.cleanupGeneratedSnapshots();
        assertTrue(Files.exists(old.file()));assertTrue(Files.exists(current.file()));assertFalse(Files.exists(garbage));
        old.close();assertFalse(Files.exists(old.file()));
        current.close();service.cleanupGeneratedSnapshots();assertTrue(Files.exists(current.file()));
    }

    @Test void failedGeneratedFileDeletionIsRetriedOnAnotherSweep()throws Exception{
        Fixture fixture=new Fixture();LegacySnapshotService service=spy(fixture.service(5_000_000));services.add(service);
        Path garbage=oldArtifact(".json");
        doThrow(new IOException("injected file lock")).doCallRealMethod().when(service).deleteGenerated(garbage);
        service.cleanupGeneratedSnapshots();assertTrue(Files.exists(garbage));
        service.cleanupGeneratedSnapshots();assertFalse(Files.exists(garbage));
        verify(service,times(2)).deleteGenerated(garbage);
    }

    @Test void anInProgressTemporaryFileIsProtectedEvenIfItsTimestampIsOld()throws Exception{
        Fixture fixture=new Fixture();LegacySnapshotService service=fixture.service(5_000_000);
        fixture.afterRead=()->{
            try(var files=Files.list(directory)){
                Path writing=files.filter(file->file.toString().endsWith(".tmp")).findFirst().orElseThrow();
                makeOld(writing);service.cleanupGeneratedSnapshots();assertTrue(Files.exists(writing));
            }catch(IOException error){throw new RuntimeException(error);}
        };
        try(var lease=service.acquire()){assertTrue(Files.exists(lease.file()));}
    }
}
