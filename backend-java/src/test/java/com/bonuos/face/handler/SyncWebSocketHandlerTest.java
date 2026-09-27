package com.bonuos.face.handler;
import org.junit.jupiter.api.Test;
import org.springframework.web.socket.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.any;
import static org.junit.jupiter.api.Assertions.*;

class SyncWebSocketHandlerTest {
    @Test void slowSessionDoesNotBlockBroadcastOrOtherDeviceAndSendsRemainSerial() throws Exception {
        SyncWebSocketHandler handler=new SyncWebSocketHandler();
        CountDownLatch blocked=new CountDownLatch(1), release=new CountDownLatch(1), delivered=new CountDownLatch(1);
        AtomicInteger active=new AtomicInteger(), maximum=new AtomicInteger();
        WebSocketSession slow=mock(WebSocketSession.class), fast=mock(WebSocketSession.class);
        when(slow.getId()).thenReturn("slow"); when(slow.isOpen()).thenReturn(true);
        when(fast.getId()).thenReturn("fast"); when(fast.isOpen()).thenReturn(true);
        doAnswer(call->{ maximum.accumulateAndGet(active.incrementAndGet(),Math::max); blocked.countDown(); release.await(3,TimeUnit.SECONDS); active.decrementAndGet(); return null; }).when(slow).sendMessage(any());
        doAnswer(call->{ delivered.countDown(); return null; }).when(fast).sendMessage(any());
        try {
            handler.afterConnectionEstablished(slow); assertTrue(blocked.await(1,TimeUnit.SECONDS));
            handler.afterConnectionEstablished(fast);
            assertTimeout(java.time.Duration.ofMillis(250),()->{ for(int i=0;i<1000;i++)handler.broadcastUpdate(); });
            assertTrue(delivered.await(1,TimeUnit.SECONDS)); assertEquals(1,maximum.get());
        } finally { release.countDown(); handler.shutdown(); }
    }
}
