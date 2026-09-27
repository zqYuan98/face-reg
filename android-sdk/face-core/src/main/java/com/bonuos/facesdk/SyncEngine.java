package com.bonuos.facesdk;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.IOException;
import java.net.URLEncoder;

/** Protocol state machine: no online writes occur before the entire bounded page sequence succeeds. */
final class SyncEngine {
    private final SyncStore store;
    private final SyncTransport transport;
    private final String identity;
    SyncEngine(SyncStore store, SyncTransport transport, String identity) {
        this.store = store; this.transport = transport; this.identity = identity;
    }

    int synchronize() throws Exception {
        JSONObject state = data("GET", "/api/sync/v2/state", null);
        String epoch = state.getString("epoch"), scope = String.valueOf(state.get("scopeVersion"));
        long head = state.getLong("head"), floor = state.getLong("retentionFloor");
        SyncStore.State local = store.state();
        if (local == null || !identity.equals(local.identity) || !epoch.equals(local.epoch) || !scope.equals(local.scopeVersion) ||
                local.revision < floor || local.revision > head) return bootstrap();
        try {
            if (local.revision == head) {
                acknowledge(epoch, scope, head, null);
                FaceDatasetCoordinator.refreshIfRequired();
                return store.count();
            }
            store.begin(true);
            replay(epoch, scope, local.revision, head, null);
            store.resolvePhotos(transport);
            int count = store.publish(new SyncStore.State(epoch, scope, head, identity));
            acknowledge(epoch, scope, head, null);
            return count;
        } catch (SyncTransport.SnapshotRequired expired) {
            return bootstrap();
        }
    }

    private int bootstrap() throws Exception {
        JSONObject start = data("POST", "/api/sync/v2/bootstrap", null);
        String lease = start.getString("leaseId");
        if (!lease.matches("[A-Za-z0-9-]{1,100}")) throw new IOException("Invalid lease ID");
        String epoch = start.getString("epoch"), scope = String.valueOf(start.get("scopeVersion"));
        long from = start.getLong("startRevision"), maxId = start.getLong("maxId"), after = 0;
        SyncStore.State previousState = store.state();
        store.begin(false, previousState != null && identity.equals(previousState.identity));
        boolean more;
        do {
            JSONObject page = data("GET", "/api/sync/v2/bootstrap/" + lease + "/page?afterId=" + after + "&limit=200", null);
            JSONArray users = page.getJSONArray("records");
            if (users.length() > 200) throw new IOException("Oversized snapshot page");
            long previous = after;
            for (int i = 0; i < users.length(); i++) {
                JSONObject user = users.getJSONObject(i);
                long id = user.getLong("id");
                if (id <= previous || id > maxId) throw new IOException("Invalid snapshot ordering");
                previous = id;
                upsert(user, user.getLong("syncVersion"));
            }
            long next = page.getLong("nextAfterId");
            more = page.getBoolean("hasMore");
            if (next < previous || next > maxId || (more && next <= after) || (!more && next != maxId)) throw new IOException("Snapshot cursor did not advance");
            after = next;
        } while (more);
        JSONObject finish = data("POST", "/api/sync/v2/bootstrap/" + lease + "/finish", null);
        if (!epoch.equals(finish.getString("epoch")) || !scope.equals(String.valueOf(finish.get("scopeVersion")))) {
            throw new SyncTransport.SnapshotRequired();
        }
        long upper = finish.getLong("upperRevision");
        replay(epoch, scope, from, upper, lease);
        store.resolvePhotos(transport);
        int count = store.publish(new SyncStore.State(epoch, scope, upper, identity));
        acknowledge(epoch, scope, upper, lease);
        return count;
    }

    private void replay(String epoch, String scope, long after, long upper, String lease) throws Exception {
        if (after < 0 || upper < after) throw new IOException("Invalid revision interval");
        while (after < upper) {
            String path = "/api/sync/v2/changes?epoch=" + encode(epoch) + "&scopeVersion=" + encode(scope) +
                    "&afterRevision=" + after + "&untilRevision=" + upper + "&limit=200" +
                    (lease == null ? "" : "&leaseId=" + encode(lease));
            JSONObject page = data("GET", path, null);
            JSONArray events = page.getJSONArray("events");
            if (events.length() > 200 || page.getLong("upperRevision") != upper) throw new IOException("Invalid changes page");
            long previous = after;
            for (int i = 0; i < events.length(); i++) {
                JSONObject event = events.getJSONObject(i);
                long revision = event.getLong("revision"), id = event.getLong("userId");
                if (revision <= previous || revision > upper) throw new IOException("Invalid change ordering");
                previous = revision;
                if ("delete".equals(event.getString("operation"))) store.delete(id, revision);
                else if ("upsert".equals(event.getString("operation"))) {
                    JSONObject user = event.getJSONObject("user");
                    if (user.getLong("id") != id || user.getLong("syncVersion") != revision) throw new IOException("Invalid postimage");
                    upsert(user, revision);
                } else throw new IOException("Unknown change operation");
            }
            long next = page.getLong("nextRevision");
            boolean more = page.getBoolean("hasMore");
            if (next <= after || next < previous || next > upper || (!more && next != upper)) throw new IOException("Changes cursor did not advance");
            after = next;
        }
    }

    private void upsert(JSONObject user, long revision) throws Exception {
        String photo = user.isNull("photoUrl") ? "" : user.optString("photoUrl", "");
        store.upsert(user, revision, photo);
    }

    int legacy() throws Exception {
        JSONObject envelope = transport.request("GET", "/api/sync/users", null);
        JSONArray records = envelope.getJSONArray("data");
        SyncStore.State previousState = store.state();
        store.begin(false, previousState != null && identity.equals(previousState.identity));
        for (int i = 0; i < records.length(); i++) upsert(records.getJSONObject(i), 0);
        store.resolvePhotos(transport);
        return store.publish(new SyncStore.State("legacy", "legacy", 0, identity));
    }

    private void acknowledge(String epoch, String scope, long revision, String lease) throws Exception {
        JSONObject request = new JSONObject().put("epoch", epoch).put("scopeVersion", Long.parseLong(scope)).put("revision", revision);
        if (lease != null) request.put("leaseId", lease);
        transport.request("POST", "/api/sync/v2/ack", request);
    }
    private JSONObject data(String method, String path, JSONObject body) throws Exception {
        if (Thread.currentThread().isInterrupted()) throw new java.io.InterruptedIOException("Sync cancelled");
        return transport.request(method, path, body).getJSONObject("data");
    }
    private static String encode(String value) throws Exception { return URLEncoder.encode(value, "UTF-8"); }
}
