package com.bonuos.face.controller;

import com.bonuos.face.service.LegacySnapshotService;
import com.bonuos.face.config.SyncDownloadAbortConfiguration;
import com.bonuos.face.exception.ApiException;
import jakarta.servlet.*;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import java.io.*;
import java.net.SocketTimeoutException;
import java.nio.file.Files;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.function.LongSupplier;

@RestController
@RequestMapping("/api/sync")
public class SyncController {
    private final LegacySnapshotService snapshots;
    @Value("${face.sync.download-timeout-ms:60000}") private long downloadTimeoutMillis = 60000;
    public SyncController(LegacySnapshotService snapshots){this.snapshots=snapshots;}
    // Legacy lastSyncTime is intentionally ignored: old SDK treats data as the COMPLETE set.
    @GetMapping("/users")
    public void syncUsers(@RequestParam(required=false) Long lastSyncTime,HttpServletRequest request,HttpServletResponse response)throws IOException {
        Object cancellation = request.getAttribute(SyncDownloadAbortConfiguration.ABORT_ATTRIBUTE);
        if (!request.isAsyncSupported() || !(cancellation instanceof Consumer<?>) || downloadTimeoutMillis < 100 || downloadTimeoutMillis > 60000) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE,"SYNC_ASYNC_UNAVAILABLE","同步下载通道不可用");
        }
        @SuppressWarnings("unchecked") Consumer<Throwable> abort = (Consumer<Throwable>) cancellation;
        var snapshot=snapshots.acquire();
        InputStream input=null;
        Download download=null;
        try {
            input=Files.newInputStream(snapshot.file());
            long length=Files.size(snapshot.file());
            response.setContentType("application/json");response.setCharacterEncoding("UTF-8");
            response.setContentLengthLong(length);
            response.setHeader("Cache-Control","no-store");
            response.setHeader("X-Sync-Revision",Long.toString(snapshot.revision()));
            response.setHeader("X-Snapshot-Created",Long.toString(snapshot.created()));
            AsyncContext async=request.startAsync(request,response);
            async.setTimeout(downloadTimeoutMillis);
            download=new Download(async,response.getOutputStream(),input,snapshot,abort,length,downloadTimeoutMillis,System::nanoTime);
            async.addListener(download);
            download.output.setWriteListener(download);
        } catch(IOException|RuntimeException error) {
            if(download!=null) download.fail(error);
            else {if(input!=null)try{input.close();}catch(IOException ignored){} snapshot.close();}
            throw error;
        }
    }

    static final class Download implements WriteListener,AsyncListener {
        private final AsyncContext async;
        private final ServletOutputStream output;
        private final InputStream input;
        private final AutoCloseable lease;
        private final Consumer<Throwable> abort;
        private final LongSupplier nowNanos;
        private final long deadlineNanos;
        private final AtomicBoolean closed=new AtomicBoolean();
        private final byte[] buffer=new byte[65536];
        private long remaining;
        Download(AsyncContext async,ServletOutputStream output,InputStream input,AutoCloseable lease,
                Consumer<Throwable> abort,long length){
            this(async,output,input,lease,abort,length,60000,System::nanoTime);
        }
        Download(AsyncContext async,ServletOutputStream output,InputStream input,AutoCloseable lease,
                Consumer<Throwable> abort,long length,long timeoutMillis,LongSupplier nowNanos){
            this.async=async;this.output=output;this.input=input;this.lease=lease;this.abort=abort;this.remaining=length;
            this.nowNanos=nowNanos;this.deadlineNanos=nowNanos.getAsLong()+TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
        }
        @Override public void onWritePossible() {
            try {
                checkDeadline();
                while(!closed.get()&&output.isReady()) {
                    // Tomcat cannot deliver onTimeout until this callback returns. Keep one fixed
                    // deadline even when a fast/always-ready socket keeps the callback running.
                    checkDeadline();
                    if(remaining==0) {
                        // Flush in non-blocking mode, then wait until Tomcat has drained every byte.
                        // Completing earlier would make SocketOutputBuffer.end() flush synchronously.
                        output.flush();
                        if(output.isReady())finish(null);
                        return;
                    }
                    int count=input.read(buffer,0,(int)Math.min(buffer.length,remaining));
                    checkDeadline();
                    if(count<0)throw new EOFException("Snapshot file truncated");
                    if(count==0)throw new IOException("Snapshot read made no progress");
                    if(closed.get())return;
                    checkDeadline();
                    output.write(buffer,0,count);
                    remaining-=count;
                }
            }catch(IOException|RuntimeException error){fail(error);}
        }
        private boolean deadlineReached(){return nowNanos.getAsLong()-deadlineNanos>=0;}
        private void checkDeadline()throws SocketTimeoutException{
            if(deadlineReached())throw new SocketTimeoutException("Sync download deadline");
        }
        private void finish(Throwable error) {
            if(error==null&&deadlineReached())error=new SocketTimeoutException("Sync download deadline");
            if(!closed.compareAndSet(false,true))return;
            try {
                if(error!=null)abort.accept(error);
            } finally {
                release();
                try{async.complete();}catch(IllegalStateException ignored){}
            }
        }
        private void release(){
            try{input.close();}catch(IOException ignored){}
            try{lease.close();}catch(Exception ignored){}
        }
        void fail(Throwable error){finish(error);}
        @Override public void onError(Throwable error){fail(error);}
        @Override public void onTimeout(AsyncEvent event){fail(new SocketTimeoutException("Sync download deadline"));}
        @Override public void onError(AsyncEvent event){fail(event.getThrowable()==null?new IOException("Sync download error"):event.getThrowable());}
        @Override public void onComplete(AsyncEvent event){if(closed.compareAndSet(false,true))release();}
        @Override public void onStartAsync(AsyncEvent event){event.getAsyncContext().addListener(this);}
    }
}
