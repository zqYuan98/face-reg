package com.bonuos.facesdk;

import org.json.JSONObject;

interface SyncTransport {
    JSONObject request(String method, String path, JSONObject body) throws Exception;
    String photo(String path, long userId, long revision) throws Exception;
    final class SnapshotRequired extends Exception { SnapshotRequired() { super("SNAPSHOT_REQUIRED"); } }
}
