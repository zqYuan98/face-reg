package com.bonuos.facesdk;

import android.content.Context;
import android.database.sqlite.SQLiteDatabase;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.Assert.*;

@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 28)
public class SyncStoreTest {
    private Context context;
    @Before public void reset() {
        context = RuntimeEnvironment.getApplication();
        context.deleteDatabase("face_sdk.db");
    }
    static JSONObject user(long id, long revision, String name, String photo) throws Exception {
        JSONArray vector = new JSONArray();
        for (int i = 0; i < 1024; i++) vector.put(0.03125);
        return new JSONObject().put("id", id).put("syncVersion", revision).put("name", name).put("status", 1)
                .put("featureData", vector.toString()).put("photoUrl", photo).put("groupId", JSONObject.NULL);
    }
    private List<FaceRepository.FaceUser> online() {
        FaceRepository repository = new FaceRepository(context);
        try { return repository.getAllUsers(); } finally { repository.close(); }
    }
    private void seed() throws Exception {
        try (SyncStore store = new SyncStore(context)) {
            store.begin(false); store.upsert(user(1, 1, "old", ""), 1, "");
            store.publish(new SyncStore.State("epoch", "1", 1, "device"));
        }
    }

    @Test public void failedOrInterruptedStagePreservesOnlineRowsAndCursorOnReopen() throws Exception {
        seed();
        try (SyncStore store = new SyncStore(context)) {
            store.begin(false); store.upsert(user(2, 2, "incomplete", ""), 2, "");
            assertEquals("old", online().get(0).name);
        }
        try (SyncStore reopened = new SyncStore(context)) {
            assertEquals(1, reopened.state().revision);
            assertEquals("old", online().get(0).name);
            reopened.begin(false);
            reopened.publish(new SyncStore.State("epoch", "1", 3, "device"));
            assertTrue(online().isEmpty());
        }
    }

    @Test public void tombstoneAndPostimageVersionsRejectOlderReplay() throws Exception {
        try (SyncStore store = new SyncStore(context)) {
            store.begin(false);
            store.upsert(user(1, 5, "newest", ""), 5, "");
            store.upsert(user(1, 2, "old", ""), 2, "");
            store.delete(1, 3);
            store.publish(new SyncStore.State("epoch", "1", 5, "device"));
            assertEquals("newest", online().get(0).name);
            store.begin(true); store.delete(1, 6);
            store.upsert(user(1, 4, "resurrected", ""), 4, "");
            store.publish(new SyncStore.State("epoch", "1", 6, "device"));
            assertTrue(online().isEmpty());
            store.begin(true); store.upsert(user(1, 5, "duplicate", ""), 5, "");
            store.publish(new SyncStore.State("epoch", "1", 6, "device"));
            assertTrue(online().isEmpty());
        }
    }

    @Test public void emptyFinalDatasetRefreshesRegisteredRecognitionCache() throws Exception {
        seed();
        AtomicInteger cachedSize = new AtomicInteger(online().size());
        Runnable cache = () -> cachedSize.set(online().size());
        FaceDatasetCoordinator.add(cache);
        try (SyncStore store = new SyncStore(context)) {
            store.begin(false);
            assertEquals(0, store.publish(new SyncStore.State("epoch", "1", 7, "device")));
            assertEquals(0, cachedSize.get());
            assertEquals(7, store.state().revision);
        } finally { FaceDatasetCoordinator.remove(cache); }
    }

    @Test public void upgradesVersionTwoWithoutDeletingExistingPeople() {
        java.io.File path = context.getDatabasePath("face_sdk.db");
        path.getParentFile().mkdirs();
        try (SQLiteDatabase db = SQLiteDatabase.openOrCreateDatabase(path, null)) {
            db.execSQL("CREATE TABLE users (id INTEGER PRIMARY KEY,user_no TEXT,name TEXT,group_id INTEGER,feature BLOB,photo_path TEXT,update_time INTEGER)");
            db.execSQL("INSERT INTO users(id,name) VALUES(8,'preserved')"); db.setVersion(2);
        }
        try (SyncStore store = new SyncStore(context)) {
            assertNull(store.state());
            assertEquals("preserved", online().get(0).name);
        }
    }

    @Test public void photoFailureCannotPublishPartialDataset() throws Exception {
        seed();
        try (SyncStore store = new SyncStore(context)) {
            store.begin(false); store.upsert(user(2, 2, "new", "new.jpg"), 2, "new.jpg");
            try { store.resolvePhotos(new StubTransport() {
                @Override public String photo(String path, long id, long revision) throws Exception { throw new IOException("missing"); }
            }); fail("expected photo failure"); } catch (IOException expected) { }
            assertEquals("old", online().get(0).name);
            assertEquals(1, store.state().revision);
        }
    }

    @Test public void bootstrapReplaysBeforeDownloadingOnlyFinalPhotos() throws Exception {
        List<String> downloaded = new ArrayList<>();
        StubTransport transport = new StubTransport() {
            @Override public JSONObject request(String method, String path, JSONObject body) throws Exception {
                if (path.equals("/api/sync/v2/state")) return wrap(new JSONObject().put("epoch", "epoch").put("scopeVersion", 1).put("head", 5).put("retentionFloor", 0));
                if (path.endsWith("/bootstrap")) return wrap(new JSONObject().put("leaseId", "lease").put("epoch", "epoch").put("scopeVersion", 1).put("startRevision", 1).put("maxId", 1));
                if (path.contains("/page?")) return wrap(new JSONObject().put("records", new JSONArray().put(user(1, 5, "new", "new.jpg"))).put("nextAfterId", 1).put("hasMore", false));
                if (path.endsWith("/finish")) return wrap(new JSONObject().put("epoch", "epoch").put("scopeVersion", 1).put("upperRevision", 5));
                if (path.contains("/changes?")) {
                    JSONArray events = new JSONArray().put(new JSONObject().put("revision", 2).put("userId", 1).put("operation", "upsert").put("user", user(1, 2, "old", "deleted.jpg")))
                            .put(new JSONObject().put("revision", 3).put("userId", 1).put("operation", "delete"));
                    return wrap(new JSONObject().put("events", events).put("nextRevision", 5).put("hasMore", false).put("upperRevision", 5));
                }
                return wrap(new JSONObject());
            }
            @Override public String photo(String path, long id, long revision) { downloaded.add(path); return "/test/new.jpg"; }
        };
        try (SyncStore store = new SyncStore(context)) {
            assertEquals(1, new SyncEngine(store, transport, "device").synchronize());
            assertEquals("new", online().get(0).name);
            assertEquals(java.util.Collections.singletonList("new.jpg"), downloaded);
            assertEquals(5, store.state().revision);
        }
    }

    @Test public void scopedEmptyEventsStillAdvanceConfirmedGlobalCursor() throws Exception {
        seed();
        StubTransport transport = new StubTransport() {
            @Override public JSONObject request(String method, String path, JSONObject body) throws Exception {
                if (path.endsWith("/state")) return wrap(new JSONObject().put("epoch", "epoch").put("scopeVersion", 1).put("head", 8).put("retentionFloor", 0));
                if (path.contains("/changes?")) return wrap(new JSONObject().put("events", new JSONArray()).put("nextRevision", 8).put("hasMore", false).put("upperRevision", 8));
                return wrap(new JSONObject());
            }
        };
        try (SyncStore store = new SyncStore(context)) {
            assertEquals(1, new SyncEngine(store, transport, "device").synchronize());
            assertEquals(8, store.state().revision);
            assertEquals("old", online().get(0).name);
        }
    }

    @Test public void rejectsCrossOriginAndEscapingPhotoUrls() {
        SyncConfig config = new SyncConfig("https://device.example", "device-1", "secret");
        assertEquals("https://device.example/uploads/one.jpg", config.photoUri("one.jpg").toString());
        for (String bad : new String[]{"https://evil.example/uploads/one.jpg", "../private", "/api/users", "//evil.example/uploads/a",
                "https://user@device.example/uploads/a.jpg", "/uploads/a.jpg?download=1", "/uploads/a.jpg#image",
                "/uploads/%2e%2e%2fsecret.jpg", "/uploads/%252e%252e%252fsecret.jpg", "/uploads/a%5Cb.jpg",
                "uuid_100%.jpg", "/uploads/uuid_100%25.jpg", "uuid_query?.jpg", "uuid_fragment#.jpg"}) {
            try { config.photoUri(bad); fail("accepted unsafe URL"); } catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void encodesLegacyUnicodeSpacesAndPlusAsOneSafePathSegment() {
        SyncConfig config = new SyncConfig("https://device.example", "device-1", "secret");
        String filename = "uuid_中文 照片+原图.jpg";
        java.net.URI uri = config.photoUri(filename);
        assertEquals("/uploads/" + filename, uri.getPath());
        assertFalse(uri.toASCIIString().contains(" "));
        assertTrue(uri.toASCIIString().contains("%20"));
        assertTrue(uri.getPath().contains("+"));
        assertEquals(uri, config.photoUri(uri.toASCIIString()));
        assertEquals(uri, config.photoUri(uri.getRawPath()));
        assertEquals(uri, config.photoUri(uri.getRawPath().replace("+", "%2B")));
    }

    @Test public void deviceSerialMatchesDatabaseSixtyFourCharacterBoundary() {
        String maximum = new String(new char[64]).replace('\0', 'a');
        assertEquals(maximum, new SyncConfig("https://device.example", maximum, "secret").deviceSn);
        try { new SyncConfig("https://device.example", maximum + "a", "secret"); fail("65-character SN accepted"); }
        catch (IllegalArgumentException expected) { }
    }

    @Test public void changingServerIdentityDoesNotReuseOldOriginsSameNamedPhoto() throws Exception {
        java.io.File oldPhoto = new java.io.File(context.getFilesDir(), "old-origin.jpg");
        try (java.io.FileOutputStream output = new java.io.FileOutputStream(oldPhoto)) { output.write(1); }
        AtomicInteger downloads = new AtomicInteger();
        try (SyncStore store = new SyncStore(context)) {
            store.begin(false); store.upsert(user(1, 1, "old", "shared.jpg"), 1, "shared.jpg");
            store.resolvePhotos(new StubTransport() {
                @Override public String photo(String path, long id, long revision) { return oldPhoto.getAbsolutePath(); }
            });
            store.publish(new SyncStore.State("epoch", "1", 1, "old-origin"));
            StubTransport transport = new StubTransport() {
                @Override public JSONObject request(String method, String path, JSONObject body) throws Exception {
                    if (path.endsWith("/state")) return wrap(new JSONObject().put("epoch", "epoch").put("scopeVersion", 1).put("head", 1).put("retentionFloor", 0));
                    if (path.endsWith("/bootstrap")) return wrap(new JSONObject().put("leaseId", "lease").put("epoch", "epoch").put("scopeVersion", 1).put("startRevision", 1).put("maxId", 1));
                    if (path.contains("/page?")) return wrap(new JSONObject().put("records", new JSONArray().put(user(1, 1, "new", "shared.jpg"))).put("nextAfterId", 1).put("hasMore", false));
                    if (path.endsWith("/finish")) return wrap(new JSONObject().put("epoch", "epoch").put("scopeVersion", 1).put("upperRevision", 1));
                    return wrap(new JSONObject());
                }
                @Override public String photo(String path, long id, long revision) { downloads.incrementAndGet(); return "/current-origin/shared.jpg"; }
            };
            assertEquals(1, new SyncEngine(store, transport, "new-origin").synchronize());
            assertEquals(1, downloads.get());
            assertEquals("/current-origin/shared.jpg", online().get(0).photoPath);
        }
    }

    @Test public void unchangedHeadDoesNotCopyDatasetOrReloadCache() throws Exception {
        seed();
        AtomicInteger publications = new AtomicInteger();
        Runnable listener = publications::incrementAndGet;
        FaceDatasetCoordinator.add(listener);
        try (SyncStore store = new SyncStore(context)) {
            StubTransport transport = new StubTransport() {
                @Override public JSONObject request(String method, String path, JSONObject body) throws Exception {
                    if (path.endsWith("/state")) return wrap(new JSONObject().put("epoch", "epoch").put("scopeVersion", 1).put("head", 1).put("retentionFloor", 0));
                    assertTrue(path.endsWith("/ack"));
                    return wrap(new JSONObject());
                }
            };
            assertEquals(1, new SyncEngine(store, transport, "device").synchronize());
            assertEquals(0, publications.get());
        } finally { FaceDatasetCoordinator.remove(listener); }
    }

    @Test public void signalDuringActiveSyncRunsAnotherCatchupInsteadOfBeingDropped() throws Exception {
        java.net.ServerSocket server = new java.net.ServerSocket(0, 16, java.net.InetAddress.getByName("127.0.0.1"));
        java.util.concurrent.CountDownLatch pageEntered = new java.util.concurrent.CountDownLatch(1), releasePage = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch completed = new java.util.concurrent.CountDownLatch(2);
        AtomicInteger stateCalls = new AtomicInteger();
        List<String> failures = new java.util.concurrent.CopyOnWriteArrayList<>();
        Thread serverThread = new Thread(() -> {
            while (!server.isClosed()) {
                try (java.net.Socket socket = server.accept()) {
                    socket.setSoTimeout(5000);
                    java.io.BufferedReader input = new java.io.BufferedReader(new java.io.InputStreamReader(socket.getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
                    String requestLine = input.readLine();
                    String path = java.net.URI.create(requestLine.split(" ")[1]).getPath();
                    boolean authorized = false;
                    String header;
                    int contentLength = 0;
                    while ((header = input.readLine()) != null && !header.isEmpty()) {
                        if (header.equalsIgnoreCase("Authorization: Bearer device.secret")) authorized = true;
                        if (header.toLowerCase(java.util.Locale.ROOT).startsWith("content-length:")) contentLength = Integer.parseInt(header.substring(15).trim());
                    }
                    for (int i = 0; i < contentLength; i++) input.read();
                    if (!authorized) throw new IOException("missing credential");
                    JSONObject data = new JSONObject();
                    if (path.endsWith("/state")) {
                        stateCalls.incrementAndGet();
                        data.put("epoch", "epoch").put("scopeVersion", 1).put("head", 0).put("retentionFloor", 0);
                    } else if (path.endsWith("/bootstrap")) data.put("leaseId", "lease").put("epoch", "epoch").put("scopeVersion", 1).put("startRevision", 0).put("maxId", 0);
                    else if (path.endsWith("/page")) {
                        pageEntered.countDown();
                        releasePage.await(3, java.util.concurrent.TimeUnit.SECONDS);
                        data.put("records", new JSONArray()).put("nextAfterId", 0).put("hasMore", false);
                    } else if (path.endsWith("/finish")) data.put("epoch", "epoch").put("scopeVersion", 1).put("upperRevision", 0);
                    byte[] body = wrap(data).toString().getBytes(java.nio.charset.StandardCharsets.UTF_8);
                    java.io.OutputStream output = socket.getOutputStream();
                    output.write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " + body.length + "\r\n\r\n").getBytes(java.nio.charset.StandardCharsets.UTF_8));
                    output.write(body); output.flush();
                } catch (Exception error) { if (!server.isClosed()) failures.add(error.getClass().getSimpleName()); }
            }
        });
        serverThread.start();
        SyncConfig config = new SyncConfig("http://127.0.0.1:" + server.getLocalPort(), "device", "secret", SyncConfig.Protocol.V2, true);
        SyncManager manager = new SyncManager(context, config);
        SyncManager.SyncCallback callback = new SyncManager.SyncCallback() {
            public void onSuccess(int count) { completed.countDown(); }
            public void onError(String message) { failures.add(message); }
        };
        try {
            manager.syncData(callback);
            assertTrue(pageEntered.await(5, java.util.concurrent.TimeUnit.SECONDS));
            manager.syncData(callback);
            releasePage.countDown();
            assertTrue(completed.await(5, java.util.concurrent.TimeUnit.SECONDS));
            assertTrue(failures.toString(), failures.isEmpty());
            assertEquals(2, stateCalls.get());
        } finally { releasePage.countDown(); manager.close(); server.close(); serverThread.join(1000); }
    }

    @Test public void stoppingAutoSyncCancelsDeferredBusyRetry() throws Exception {
        java.lang.reflect.Field lockField = SyncManager.class.getDeclaredField("DATABASE_SYNC");
        lockField.setAccessible(true);
        java.util.concurrent.Semaphore busyDatabase = (java.util.concurrent.Semaphore) lockField.get(null);
        java.lang.reflect.Field retryField = SyncManager.class.getDeclaredField("retry");
        retryField.setAccessible(true);
        boolean held = false;
        try (java.net.ServerSocket server = new java.net.ServerSocket(0, 16, java.net.InetAddress.getByName("127.0.0.1"))) {
            SyncManager manager = new SyncManager(context, new SyncConfig("http://127.0.0.1:" + server.getLocalPort(),
                    "device", "secret", SyncConfig.Protocol.V2, true));
            try {
                assertTrue(busyDatabase.tryAcquire()); held = true;
                manager.syncData(null);
                boolean queued = false;
                for (int i = 0; i < 100; i++) {
                    synchronized (manager) { queued = retryField.get(manager) != null; }
                    if (queued) break;
                    Thread.sleep(10);
                }
                assertTrue("Expected a deferred retry while another manager owns staging", queued);
                manager.stopAutoSync();
                busyDatabase.release(); held = false;
                server.setSoTimeout(1500);
                try (java.net.Socket unexpected = server.accept()) {
                    fail("Paused manager restarted network synchronization");
                } catch (java.net.SocketTimeoutException expected) { }
            } finally { manager.close(); if (held) busyDatabase.release(); }
        }
    }

    @Test public void stopBetweenPendingConsumptionAndBusyResultCannotRestoreRetry() throws Exception {
        java.util.concurrent.CountDownLatch enteringAcquire = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch returnBusy = new java.util.concurrent.CountDownLatch(1);
        AtomicInteger attempts = new AtomicInteger();
        java.util.concurrent.Semaphore delayedBusy = new java.util.concurrent.Semaphore(0) {
            @Override public boolean tryAcquire() {
                attempts.incrementAndGet(); enteringAcquire.countDown();
                try { returnBusy.await(3, java.util.concurrent.TimeUnit.SECONDS); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
                return false;
            }
        };
        SyncManager manager = new SyncManager(context,
                new SyncConfig("http://127.0.0.1:1", "device", "secret", SyncConfig.Protocol.V2, true), delayedBusy);
        try {
            manager.syncData(null);
            assertTrue(enteringAcquire.await(3, java.util.concurrent.TimeUnit.SECONDS));
            manager.stopAutoSync();
            returnBusy.countDown();
            Thread.sleep(1300);
            assertEquals("Stopped generation must not restore pending or queue another attempt", 1, attempts.get());
        } finally { returnBusy.countDown(); manager.close(); }
    }

    static JSONObject wrap(JSONObject data) throws Exception { return new JSONObject().put("code", 200).put("data", data); }
    static class StubTransport implements SyncTransport {
        @Override public JSONObject request(String method, String path, JSONObject body) throws Exception { return wrap(new JSONObject()); }
        @Override public String photo(String path, long id, long revision) throws Exception { return ""; }
    }
}
