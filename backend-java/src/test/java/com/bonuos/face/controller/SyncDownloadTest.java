package com.bonuos.face.controller;

import jakarta.servlet.AsyncContext;
import jakarta.servlet.AsyncEvent;
import jakarta.servlet.ServletOutputStream;
import jakarta.servlet.WriteListener;
import org.junit.jupiter.api.Test;
import java.io.*;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class SyncDownloadTest {
    @Test void backpressureStopsReadsUntilReadyAndPreservesCompleteBytes() throws Exception {
        byte[] expected = new byte[150000]; Arrays.fill(expected,(byte)'a');
        ByteArrayInputStream input = new ByteArrayInputStream(expected);
        var output = new TestOutput();
        output.stopAfterWrite=true;
        var async=mock(AsyncContext.class);
        AtomicInteger closes=new AtomicInteger(),aborts=new AtomicInteger();
        var download=new SyncController.Download(async,output,input,closes::incrementAndGet,error->aborts.incrementAndGet(),expected.length);
        download.onWritePossible();
        assertThat(output.bytes.size()).isEqualTo(65536);
        int unread=input.available();
        download.onWritePossible();
        assertThat(input.available()).isEqualTo(unread);
        verify(async,never()).complete();
        output.ready=true;output.stopAfterWrite=false;
        download.onWritePossible();
        assertThat(output.bytes.toByteArray()).isEqualTo(expected);
        assertThat(output.maxWrite).isLessThanOrEqualTo(65536);
        assertThat(closes).hasValue(1);assertThat(aborts).hasValue(0);
        verify(async).complete();
    }

    @Test void completionWaitsForNonBlockingFlushToDrain() {
        var output=new TestOutput();output.stopOnFlush=true;
        var async=mock(AsyncContext.class);
        AtomicInteger closes=new AtomicInteger();
        var download=new SyncController.Download(async,output,new ByteArrayInputStream(new byte[]{1}),closes::incrementAndGet,error->{},1);
        download.onWritePossible();
        verify(async,never()).complete();assertThat(closes).hasValue(0);
        output.stopOnFlush=false;output.ready=true;
        download.onWritePossible();
        verify(async).complete();assertThat(closes).hasValue(1);
    }

    @Test void timeoutAbortsBeforeCompletionAndReleasesExactlyOnce() {
        var async=mock(AsyncContext.class);
        var events=new java.util.ArrayList<String>();
        doAnswer(call->{events.add("complete");return null;}).when(async).complete();
        AtomicInteger inputCloses=new AtomicInteger(),leases=new AtomicInteger();
        InputStream input=new ByteArrayInputStream(new byte[0]){@Override public void close(){inputCloses.incrementAndGet();}};
        var download=new SyncController.Download(async,new TestOutput(),input,leases::incrementAndGet,error->events.add("abort"),0);
        download.onTimeout(new AsyncEvent(async));
        download.onError(new IOException("late"));
        download.onComplete(new AsyncEvent(async));
        assertThat(events).containsExactly("abort","complete");
        assertThat(inputCloses).hasValue(1);assertThat(leases).hasValue(1);
        verify(async,times(1)).complete();
    }

    @Test void truncatedFileAndNetworkErrorsDoNotFinishAsSuccessfulDownloads() {
        var async=mock(AsyncContext.class);
        AtomicInteger failures=new AtomicInteger(),leases=new AtomicInteger();
        var download=new SyncController.Download(async,new TestOutput(),new ByteArrayInputStream(new byte[0]),
                leases::incrementAndGet,error->failures.incrementAndGet(),100);
        download.onWritePossible();
        assertThat(failures).hasValue(1);assertThat(leases).hasValue(1);
        verify(async).complete();
    }

    @Test void alwaysReadyCallbackCannotExtendAbsoluteDeadline() {
        var async=mock(AsyncContext.class);
        var output=new TestOutput();
        AtomicLong clock=new AtomicLong();
        AtomicInteger inputCloses=new AtomicInteger(),leases=new AtomicInteger();
        AtomicReference<Throwable> failure=new AtomicReference<>();
        InputStream input=new ByteArrayInputStream(new byte[65536*3]){
            @Override public synchronized int read(byte[] bytes,int offset,int length){
                int count=super.read(bytes,offset,length);
                clock.addAndGet(java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(60));
                return count;
            }
            @Override public void close(){inputCloses.incrementAndGet();}
        };
        var download=new SyncController.Download(async,output,input,leases::incrementAndGet,
                failure::set,65536*3,100,clock::get);
        download.onWritePossible();
        assertThat(output.ready).isTrue();
        assertThat(output.bytes.size()).as("second read crosses the fixed deadline before its write").isEqualTo(65536);
        assertThat(failure.get()).isInstanceOf(java.net.SocketTimeoutException.class);
        download.onComplete(new AsyncEvent(async));
        assertThat(inputCloses).hasValue(1);assertThat(leases).hasValue(1);
        verify(async,times(1)).complete();
    }

    @Test void successfulFinishRechecksDeadlineAfterFinalFlush() {
        var async=mock(AsyncContext.class);
        AtomicLong clock=new AtomicLong();
        var output=new TestOutput(){
            @Override public void flush(){clock.set(java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(100));}
        };
        AtomicInteger leases=new AtomicInteger();
        AtomicReference<Throwable> failure=new AtomicReference<>();
        var download=new SyncController.Download(async,output,new ByteArrayInputStream(new byte[]{1}),
                leases::incrementAndGet,failure::set,1,100,clock::get);
        download.onWritePossible();
        assertThat(failure.get()).isInstanceOf(java.net.SocketTimeoutException.class);
        assertThat(leases).hasValue(1);verify(async).complete();
    }

    private static class TestOutput extends ServletOutputStream {
        final ByteArrayOutputStream bytes=new ByteArrayOutputStream();
        boolean ready=true,stopAfterWrite,stopOnFlush;
        int maxWrite;
        @Override public boolean isReady(){return ready;}
        @Override public void setWriteListener(WriteListener listener){}
        @Override public void write(int value){bytes.write(value);}
        @Override public void write(byte[] value,int offset,int length){
            if(!ready)throw new IllegalStateException("Write while not ready");
            maxWrite=Math.max(maxWrite,length);bytes.write(value,offset,length);
            if(stopAfterWrite)ready=false;
        }
        @Override public void flush(){if(stopOnFlush)ready=false;}
    }
}
