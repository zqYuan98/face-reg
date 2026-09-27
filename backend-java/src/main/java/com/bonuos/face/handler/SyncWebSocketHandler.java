package com.bonuos.face.handler;

import jakarta.annotation.PreDestroy;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.*;
import org.springframework.web.socket.adapter.standard.StandardWebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Notifications are hints: coalesce them; the revision log is the source of truth. */
@Component
public class SyncWebSocketHandler extends TextWebSocketHandler {
    private static final int MAX_SESSIONS = 64;
    private final ConcurrentMap<String, Connection> sessions = new ConcurrentHashMap<>();
    private final ThreadPoolExecutor sender = new ThreadPoolExecutor(8,8,0,TimeUnit.SECONDS,
            new ArrayBlockingQueue<>(MAX_SESSIONS), runnable -> {
                Thread thread = new Thread(runnable,"sync-notification"); thread.setDaemon(true); return thread;
            },new ThreadPoolExecutor.AbortPolicy());

    private static final class Connection {
        final WebSocketSession socket;
        final AtomicBoolean pending = new AtomicBoolean();
        final AtomicBoolean scheduled = new AtomicBoolean();
        Connection(WebSocketSession socket) {
            this.socket = new ConcurrentWebSocketSessionDecorator(socket,5000,1024);
        }
    }
    @Override public synchronized void afterConnectionEstablished(WebSocketSession session) throws Exception {
        if (sessions.size() >= MAX_SESSIONS) { session.close(new CloseStatus(1013,"Server busy")); return; }
        if (session instanceof StandardWebSocketSession standard) {
            var nativeSession = standard.getNativeSession();
            nativeSession.getUserProperties().put("org.apache.tomcat.websocket.BLOCKING_SEND_TIMEOUT",5000L);
            nativeSession.setMaxIdleTimeout(120000);
        }
        Connection connection = new Connection(session);
        sessions.put(session.getId(),connection);
        connection.pending.set(true); schedule(connection);
    }
    @Override public void afterConnectionClosed(WebSocketSession session, CloseStatus status) { sessions.remove(session.getId()); }
    @Override public void handleTransportError(WebSocketSession session, Throwable error) {
        Connection connection = sessions.remove(session.getId());
        if (connection != null) close(connection);
    }
    public void broadcastUpdate() {
        sessions.values().forEach(connection -> { connection.pending.set(true); schedule(connection); });
    }
    private void schedule(Connection connection) {
        if (!connection.scheduled.compareAndSet(false,true)) return;
        try { sender.execute(() -> drain(connection)); }
        catch (RejectedExecutionException e) { connection.scheduled.set(false); }
    }
    private void drain(Connection connection) {
        try {
            if (connection.pending.getAndSet(false) && connection.socket.isOpen())
                connection.socket.sendMessage(new TextMessage("UPDATE"));
        } catch (Exception e) { close(connection); }
        finally {
            connection.scheduled.set(false);
            if (connection.pending.get() && sessions.containsKey(connection.socket.getId())) schedule(connection);
        }
    }
    private void close(Connection connection) {
        sessions.remove(connection.socket.getId(),connection);
        try { connection.socket.close(CloseStatus.SESSION_NOT_RELIABLE); } catch (Exception ignored) { }
    }
    @PreDestroy public void shutdown() { sender.shutdownNow(); sessions.values().forEach(this::close); }
}
