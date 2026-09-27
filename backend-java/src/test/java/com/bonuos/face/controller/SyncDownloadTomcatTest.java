package com.bonuos.face.controller;

import com.bonuos.face.config.SyncDownloadAbortConfiguration;
import com.bonuos.face.service.LegacySnapshotService;
import jakarta.servlet.http.HttpServlet;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.apache.catalina.Context;
import org.apache.catalina.startup.Tomcat;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.test.util.ReflectionTestUtils;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.concurrent.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

/** Real Tomcat NIO: an unread large response must abort and return the snapshot lease. */
class SyncDownloadTomcatTest {
    @TempDir Path directory;
    @Test void asyncDeadlineClosesSlowSocketWithoutBlockingFinalFlush() throws Exception {
        Path snapshot=directory.resolve("large.json");
        try(OutputStream file=Files.newOutputStream(snapshot)){
            file.write("{\"code\":200,\"data\":[\"".getBytes(StandardCharsets.UTF_8));
            byte[] block=new byte[65536];java.util.Arrays.fill(block,(byte)' ');
            for(int i=0;i<512;i++)file.write(block);
            file.write("\"]}".getBytes(StandardCharsets.UTF_8));
        }
        var snapshots=mock(LegacySnapshotService.class);
        var lease=mock(LegacySnapshotService.Lease.class);
        when(snapshots.acquire()).thenReturn(lease);when(lease.file()).thenReturn(snapshot);
        CountDownLatch released=new CountDownLatch(1);
        doAnswer(call->{released.countDown();return null;}).when(lease).close();
        SyncController controller=new SyncController(snapshots);
        ReflectionTestUtils.setField(controller,"downloadTimeoutMillis",250L);
        Tomcat server=new Tomcat();
        server.setBaseDir(directory.resolve("tomcat").toString());server.setPort(0);
        var connector=server.getConnector();
        connector.setProperty("address","127.0.0.1");
        connector.setProperty("connectionTimeout","10000");
        connector.setProperty("socket.txBufSize","1024");
        Context context=server.addContext("",directory.toString());
        context.getPipeline().addValve(new SyncDownloadAbortConfiguration.AbortValve());
        var servlet=Tomcat.addServlet(context,"sync",new HttpServlet(){
            @Override protected void doGet(HttpServletRequest request,HttpServletResponse response)throws IOException{
                controller.syncUsers(null,request,response);
            }
        });
        servlet.setAsyncSupported(true);context.addServletMappingDecoded("/api/sync/users","sync");
        try {
            server.start();
            try(Socket client=new Socket()){
                client.setReceiveBufferSize(1024);client.connect(new InetSocketAddress("127.0.0.1",connector.getLocalPort()));
                client.getOutputStream().write("GET /api/sync/users HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".getBytes(StandardCharsets.US_ASCII));
                client.getOutputStream().flush();
                // Read no response while the server fills the tiny TCP window.
                assertThat(released.await(4,TimeUnit.SECONDS)).as("async deadline returns lease without a blocking final flush").isTrue();
                client.setSoTimeout(3000);
                long received=0;byte[] buffer=new byte[8192];
                boolean disconnected=false;
                try {
                    int size;while((size=client.getInputStream().read(buffer))!=-1){
                        received+=size;
                        assertThat(received).as("timed-out socket cannot continue sending the snapshot").isLessThanOrEqualTo(1024*1024);
                    }
                    disconnected=true;
                }catch(SocketTimeoutException error){throw new AssertionError("Server did not close timed-out connection",error);}
                catch(SocketException reset){disconnected=true;/* CLOSE_NOW may reset instead of sending FIN. */}
                assertThat(disconnected).as("server closes with EOF or a TCP reset").isTrue();
                assertThat(received).isLessThan(Files.size(snapshot));
                verify(lease,times(1)).close();
            }
        } finally {server.stop();server.destroy();}
    }
}
