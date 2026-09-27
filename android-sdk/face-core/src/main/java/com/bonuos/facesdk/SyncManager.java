package com.bonuos.facesdk;

import android.content.Context;
import android.util.Log;
import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;
import java.io.File;
import java.net.URI;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.*;

/** Configurable dual-protocol sync. WebSocket messages only request a durable cursor catch-up. */
public class SyncManager implements AutoCloseable {
    private static final String TAG = "SyncManager";
    private static final Semaphore DATABASE_SYNC = new Semaphore(1);
    private static volatile SyncConfig defaults;
    private final Context context;
    private final SyncConfig config;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor();
    private final SyncHttpClient http;
    private final Semaphore databaseSync;
    private boolean running, pending, auto, closed;
    private long lifecycleGeneration;
    private SyncCallback callback;
    private Future<?> task;
    private ScheduledFuture<?> periodic, reconnect, retry;
    private WebSocketClient socket;

    public interface SyncCallback {
        /** Complete online dataset size, including zero. Called only after dataset/cache publication. */
        void onSuccess(int count);
        void onError(String msg);
    }

    public static void configure(SyncConfig config) {
        if (config == null) throw new IllegalArgumentException("Sync configuration is required");
        defaults = config;
    }

    /** Preserves the public API; provision configure() before constructing managers. */
    public SyncManager(Context context) { this(context, defaults); }
    public SyncManager(Context context, SyncConfig config) {
        this(context, config, DATABASE_SYNC);
    }
    SyncManager(Context context, SyncConfig config, Semaphore databaseSync) {
        this.context = context.getApplicationContext();
        this.config = config;
        this.databaseSync = databaseSync;
        http = config == null ? null : new SyncHttpClient(config, new File(this.context.getFilesDir(), "faces"));
    }

    public synchronized void syncData(SyncCallback callback) {
        if (closed) return;
        if (config == null) {
            if (callback != null) callback.onError("请先配置同步服务器和设备凭据");
            return;
        }
        this.callback = callback;
        pending = true;
        if (running) return;
        running = true;
        task = worker.submit(this::drain);
    }

    private void drain() {
        try {
            while (!Thread.currentThread().isInterrupted()) {
                long generation;
                synchronized (this) {
                    if (closed || !pending) return;
                    pending = false;
                    generation = lifecycleGeneration;
                }
                if (!databaseSync.tryAcquire()) {
                    synchronized (this) {
                        if (!closed && generation == lifecycleGeneration) pending = true;
                    }
                    return;
                }
                try (SyncStore store = new SyncStore(context)) {
                    synchronized (this) {
                        if (closed || generation != lifecycleGeneration) return;
                    }
                    String identity = config.baseUri + "|" + config.deviceSn + "|" + config.protocol;
                    SyncEngine engine = new SyncEngine(store, http, identity);
                    int count = config.protocol == SyncConfig.Protocol.V2 ? engine.synchronize() : engine.legacy();
                    SyncCallback listener = currentCallback();
                    if (listener != null && !Thread.currentThread().isInterrupted()) listener.onSuccess(count);
                } catch (Exception error) {
                    Log.w(TAG, "Synchronization failed: " + error.getClass().getSimpleName());
                    SyncCallback listener = currentCallback();
                    if (listener != null && !Thread.currentThread().isInterrupted()) listener.onError("同步未完成，请检查连接或设备授权");
                    return;
                } finally { databaseSync.release(); }
            }
        } finally {
            synchronized (this) {
                running = false;
                // A notification racing with the final loop check is retained and coalesced.
                if (pending && !closed) {
                    long generation = lifecycleGeneration;
                    retry = scheduler.schedule(() -> {
                        synchronized (SyncManager.this) {
                            if (closed || generation != lifecycleGeneration || !pending) return;
                            retry = null;
                            syncData(callback);
                        }
                    }, 1, TimeUnit.SECONDS);
                }
            }
        }
    }

    private synchronized SyncCallback currentCallback() { return callback; }

    public synchronized void startAutoSync(long intervalSeconds, SyncCallback callback) {
        if (closed) return;
        auto = true;
        long generation = lifecycleGeneration;
        if (periodic != null) periodic.cancel(false);
        periodic = scheduler.scheduleWithFixedDelay(() -> triggerAuto(generation), Math.max(5, intervalSeconds),
                Math.max(5, intervalSeconds), TimeUnit.SECONDS);
        syncData(callback);
        if (config != null && socket == null) connectWebSocket();
    }

    public synchronized void stopAutoSync() {
        auto = false;
        lifecycleGeneration++;
        pending = false;
        if (periodic != null) { periodic.cancel(false); periodic = null; }
        if (reconnect != null) { reconnect.cancel(false); reconnect = null; }
        if (retry != null) { retry.cancel(false); retry = null; }
        WebSocketClient previous = socket;
        socket = null;
        if (previous != null) previous.close();
    }

    private synchronized void triggerAuto(long generation) {
        if (auto && !closed && generation == lifecycleGeneration) syncData(callback);
    }

    private synchronized void connectWebSocket() {
        if (!auto || closed || config == null || socket != null) return;
        long generation = lifecycleGeneration;
        URI base = config.baseUri;
        URI uri = URI.create(("https".equals(base.getScheme()) ? "wss" : "ws") + "://" + base.getRawAuthority() + "/ws/sync");
        Map<String, String> headers = new HashMap<>();
        headers.put("X-Device-SN", config.deviceSn);
        if (config.credential != null) headers.put("Authorization", "Bearer " + config.credential);
        socket = new WebSocketClient(uri, headers) {
            @Override public void onOpen(ServerHandshake handshake) {
                synchronized (SyncManager.this) { if (socket == this) triggerAuto(generation); }
            }
            @Override public void onMessage(String message) {
                synchronized (SyncManager.this) { if (socket == this && "UPDATE".equals(message)) triggerAuto(generation); }
            }
            @Override public void onClose(int code, String reason, boolean remote) {
                synchronized (SyncManager.this) {
                    if (socket == this) socket = null;
                    if (auto && !closed && generation == lifecycleGeneration && socket == null) {
                        reconnect = scheduler.schedule(() -> {
                            synchronized (SyncManager.this) {
                                if (auto && !closed && generation == lifecycleGeneration) connectWebSocket();
                            }
                        }, 5, TimeUnit.SECONDS);
                    }
                }
            }
            @Override public void onError(Exception error) { Log.w(TAG, "Sync notification connection unavailable"); }
        };
        socket.setConnectionLostTimeout(30);
        socket.connect();
    }

    @Override public synchronized void close() {
        if (closed) return;
        stopAutoSync();
        closed = true;
        if (task != null) task.cancel(true);
        if (http != null) http.close();
        worker.shutdownNow();
        scheduler.shutdownNow();
    }
}
