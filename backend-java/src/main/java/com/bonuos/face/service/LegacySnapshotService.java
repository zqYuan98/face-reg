package com.bonuos.face.service;

import com.bonuos.face.entity.User;
import com.bonuos.face.exception.ApiException;
import com.fasterxml.jackson.core.JsonGenerator;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.stereotype.Service;
import org.springframework.scheduling.annotation.Scheduled;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.transaction.*;
import org.springframework.transaction.support.TransactionTemplate;
import java.io.*;
import java.nio.file.*;
import java.sql.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.Semaphore;
import java.util.regex.Pattern;

/** v1 gets a complete array. Bounded SQL pages share one RR snapshot; completed disk files are reused. */
@Service
public class LegacySnapshotService {
    private static final Logger log=LoggerFactory.getLogger(LegacySnapshotService.class);
    private static final int CHUNK_SIZE=200;
    private static final Pattern GENERATED=Pattern.compile("snapshot-(?:[0-9]{1,30}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\\.(?:json|tmp)");
    private final JdbcTemplate jdbc; private final ObjectMapper json; private final TransactionTemplate transaction;
    private final Path directory; private final long maxBytes;
    private final Semaphore generation=new Semaphore(1), downloads=new Semaphore(2);
    private final Set<Path> protectedFiles=new HashSet<>();
    private DirectoryStream<Path> cleanupScan;
    private Iterator<Path> cleanupEntries;
    @Value("${face.sync.snapshot-cleanup-grace-ms:3600000}") private long cleanupGraceMillis=3600000;
    private Snapshot current;
    private static final class Snapshot {
        final Path file; final String epoch; final long head; final long created=System.currentTimeMillis();
        int readers; boolean retired;
        Snapshot(Path file,String epoch,long head){this.file=file;this.epoch=epoch;this.head=head;}
    }
    public LegacySnapshotService(JdbcTemplate jdbc,ObjectMapper json,PlatformTransactionManager manager,
            @Value("${face.sync.snapshot-dir:./data/snapshots}") String directory,
            @Value("${face.sync.snapshot-max-bytes:2147483648}") long maxBytes) {
        if(maxBytes<1)throw new IllegalArgumentException("Snapshot byte limit must be positive");
        this.jdbc=jdbc;this.json=json;this.directory=Path.of(directory).toAbsolutePath().normalize();this.maxBytes=maxBytes;
        transaction=new TransactionTemplate(manager);transaction.setReadOnly(true);
        transaction.setIsolationLevel(TransactionDefinition.ISOLATION_REPEATABLE_READ);transaction.setTimeout(60);
    }
    public final class Lease implements AutoCloseable {
        private final Snapshot snapshot; private boolean closed;
        private Lease(Snapshot snapshot){this.snapshot=snapshot;}
        public Path file(){return snapshot.file;}
        public long revision(){return snapshot.head;}
        public long created(){return snapshot.created;}
        @Override public void close(){synchronized(LegacySnapshotService.this){if(closed)return;closed=true;snapshot.readers--;discardRetired(snapshot);}downloads.release();}
    }
    public Lease acquire() {
        if(!downloads.tryAcquire())throw busy();
        try {
            var clock=jdbc.queryForMap("SELECT epoch,head FROM sync_revision WHERE id=1");
            String epoch=(String)clock.get("epoch");long head=((Number)clock.get("head")).longValue();
            synchronized(this){if(current!=null&&current.epoch.equals(epoch)&&current.head==head&&System.currentTimeMillis()-current.created<60000){current.readers++;return new Lease(current);}}
            if(!generation.tryAcquire())throw busy();
            try {
                Snapshot replacement=generate();
                synchronized(this){Snapshot old=current;current=replacement;current.readers++;if(old!=null){old.retired=true;discardRetired(old);}return new Lease(current);}
            } finally {generation.release();}
        } catch(RuntimeException e){downloads.release();throw e;}
    }
    private Snapshot generate() {
        Path temporary=null,published=null;
        try {
            long deadline=nowNanos()+Duration.ofSeconds(60).toNanos();
            Files.createDirectories(directory);
            temporary=directory.resolve("snapshot-"+UUID.randomUUID()+".tmp");
            synchronized(this){protectedFiles.add(temporary);}
            Files.createFile(temporary);
            Path writing=temporary;
            Snapshot generated=transaction.execute(status->{
                checkDeadline(deadline);
                var clock=jdbc.queryForMap("SELECT epoch,head FROM sync_revision WHERE id=1");
                long head=((Number)clock.get("head")).longValue();String epoch=(String)clock.get("epoch");
                try(OutputStream file=Files.newOutputStream(writing);OutputStream bounded=new LimitedOutput(file,maxBytes);
                    JsonGenerator writer=json.getFactory().createGenerator(bounded)) {
                    writer.writeStartObject();writer.writeNumberField("code",200);writer.writeStringField("msg","success");
                    writer.writeNumberField("timestamp",System.currentTimeMillis());writer.writeStringField("epoch",epoch);writer.writeNumberField("revision",head);writer.writeArrayFieldStart("data");
                    long afterId=0;
                    while(true) {
                        checkDeadline(deadline);
                        List<User> chunk=readChunk(afterId);
                        checkDeadline(deadline);
                        for(User user:chunk){checkDeadline(deadline);writer.writeObject(user);afterId=user.getId();}
                        if(chunk.size()<CHUNK_SIZE)break;
                    }
                    writer.writeEndArray();writer.writeEndObject();
                }catch(IOException e){throw new UncheckedIOException(e);}
                return new Snapshot(writing,epoch,head);
            });
            checkDeadline(deadline);
            published=directory.resolve("snapshot-"+UUID.randomUUID()+".json");
            synchronized(this){protectedFiles.add(published);}
            Files.move(temporary,published,StandardCopyOption.ATOMIC_MOVE);
            synchronized(this){protectedFiles.remove(temporary);}
            return new Snapshot(published,generated.epoch,generated.head);
        }catch(Exception e){
            releaseGenerated(temporary);releaseGenerated(published);
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE,"SNAPSHOT_UNAVAILABLE","完整同步快照暂不可用，请稍后重试");
        }
    }
    private List<User> readChunk(long afterId) {
        return jdbc.execute((ConnectionCallback<List<User>>)connection->{
            // No streaming result: the driver can buffer at most 200 rows and close never drains the entire table.
            try(PreparedStatement statement=connection.prepareStatement("SELECT id,user_no,name,group_id,feature_data,photo_url,status,create_time,update_time,sync_version "
                    +"FROM sys_user WHERE status=1 AND id>? ORDER BY id LIMIT ?")) {
                statement.setLong(1,afterId);statement.setInt(2,CHUNK_SIZE);statement.setQueryTimeout(5);
                List<User> result=new ArrayList<>(CHUNK_SIZE);
                try(ResultSet rows=statement.executeQuery()){while(rows.next()){
                    User user=new User();user.setId(rows.getLong(1));user.setUserNo(rows.getString(2));user.setName(rows.getString(3));user.setGroupId(rows.getObject(4,Long.class));
                    user.setFeatureData(rows.getString(5));user.setPhotoUrl(rows.getString(6));user.setStatus(rows.getInt(7));
                    user.setCreateTime(rows.getObject(8,java.time.LocalDateTime.class));user.setUpdateTime(rows.getObject(9,java.time.LocalDateTime.class));user.setSyncVersion(rows.getLong(10));
                    result.add(user);
                }}
                return result;
            }
        });
    }
    long nowNanos(){return System.nanoTime();}
    private void checkDeadline(long deadline){if(Thread.currentThread().isInterrupted()||nowNanos()>=deadline)throw new IllegalStateException("Snapshot deadline exceeded");}
    private void discardRetired(Snapshot snapshot){if(snapshot.retired&&snapshot.readers==0)releaseGenerated(snapshot.file);}
    private void releaseGenerated(Path file){
        if(file==null)return;
        synchronized(this){protectedFiles.remove(file);}
        try{deleteGenerated(file);}catch(IOException e){log.warn("Generated snapshot cleanup deferred");}
    }
    void deleteGenerated(Path file)throws IOException{Files.deleteIfExists(file);}
    /** Private generated directory only. Keep the scan cursor so young/protected entries cannot starve old files. */
    @Scheduled(initialDelayString="${face.sync.snapshot-cleanup-interval-ms:60000}",fixedDelayString="${face.sync.snapshot-cleanup-interval-ms:60000}")
    public synchronized void cleanupGeneratedSnapshots(){
        if(!Files.isDirectory(directory)||Files.isSymbolicLink(directory))return;
        // At least two minutes protects a just-finished file left by a recently stopped process.
        long cutoff=System.currentTimeMillis()-Math.max(120000,cleanupGraceMillis);
        try{
            if(cleanupScan==null){cleanupScan=Files.newDirectoryStream(directory);cleanupEntries=cleanupScan.iterator();}
            int inspected=0,attempted=0;
            while(inspected<100&&attempted<20&&cleanupEntries.hasNext()){
                Path file=cleanupEntries.next();inspected++;
                if(!GENERATED.matcher(file.getFileName().toString()).matches()||protectedFiles.contains(file)
                        ||!Files.isRegularFile(file,LinkOption.NOFOLLOW_LINKS)||Files.getLastModifiedTime(file,LinkOption.NOFOLLOW_LINKS).toMillis()>cutoff)continue;
                attempted++;
                try{deleteGenerated(file);}catch(IOException e){log.warn("Generated snapshot cleanup retry deferred");}
            }
            if(!cleanupEntries.hasNext())closeCleanupScan();
        }catch(IOException|DirectoryIteratorException e){log.warn("Generated snapshot cleanup scan deferred");closeCleanupScan();}
    }
    @PreDestroy public synchronized void closeCleanupScan(){
        if(cleanupScan!=null)try{cleanupScan.close();}catch(IOException ignored){}
        cleanupScan=null;cleanupEntries=null;
    }
    private static ApiException busy(){return new ApiException(HttpStatus.TOO_MANY_REQUESTS,"SYNC_BUSY","同步繁忙，请稍后重试");}
    private static final class LimitedOutput extends FilterOutputStream {
        private long remaining;LimitedOutput(OutputStream output,long limit){super(output);remaining=limit;}
        @Override public void write(byte[] value,int offset,int length)throws IOException{if(length>remaining)throw new IOException("Snapshot byte limit");out.write(value,offset,length);remaining-=length;}
        @Override public void write(int value)throws IOException{if(remaining--<=0)throw new IOException("Snapshot byte limit");out.write(value);}
    }
}
