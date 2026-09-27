package com.bonuos.facesdk;

import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.*;

final class SyncHttpClient implements SyncTransport, AutoCloseable {
    private final SyncConfig config;
    private final File photos;
    private final ScheduledExecutorService deadlines = Executors.newSingleThreadScheduledExecutor();
    private volatile HttpURLConnection active;
    SyncHttpClient(SyncConfig config, File photos) { this.config = config; this.photos = photos; }

    @Override public JSONObject request(String method, String path, JSONObject body) throws Exception {
        URI uri = config.baseUri.resolve(path);
        HttpURLConnection connection = open(uri, method);
        ScheduledFuture<?> timeout = deadlines.schedule(connection::disconnect, 15, TimeUnit.SECONDS);
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
        try {
            if (body != null) {
                byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(payload.length);
                connection.setRequestProperty("Content-Type", "application/json");
                try (OutputStream output = connection.getOutputStream()) { output.write(payload); }
            }
            int status = connection.getResponseCode();
            InputStream response = status < 400 ? connection.getInputStream() : connection.getErrorStream();
            if (response == null) throw new IOException("Sync HTTP " + status);
            byte[] bytes;
            try (InputStream input = response; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                copy(input, output, 16 * 1024 * 1024, deadline);
                bytes = output.toByteArray();
            }
            JSONObject envelope = new JSONObject(new String(bytes, StandardCharsets.UTF_8));
            if (status == 409 && "SNAPSHOT_REQUIRED".equals(envelope.optString("errorCode"))) throw new SnapshotRequired();
            if (status != 200 || envelope.optInt("code", -1) != 200) throw new IOException("Sync HTTP " + status);
            return envelope;
        } finally { timeout.cancel(false); connection.disconnect(); active = null; }
    }

    @Override public String photo(String path, long userId, long revision) throws Exception {
        if (path == null || path.isEmpty()) return "";
        URI uri = config.photoUri(path);
        if (!photos.isDirectory() && !photos.mkdirs()) throw new IOException("Cannot create photo directory");
        // A scope/epoch reset can reuse row revisions: include URL identity so an old photo is not reused.
        byte[] digest = java.security.MessageDigest.getInstance("SHA-256").digest(uri.toASCIIString().getBytes(StandardCharsets.UTF_8));
        StringBuilder hash = new StringBuilder();
        for (byte value : digest) hash.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        String key = hash.toString();
        File target = new File(photos, userId + "-" + revision + "-" + key + ".jpg");
        if (target.isFile() && target.length() > 0) return target.getAbsolutePath();
        File partial = new File(photos, "pending-" + UUID.randomUUID() + ".part");
        HttpURLConnection connection = open(uri, "GET");
        ScheduledFuture<?> timeout = deadlines.schedule(connection::disconnect, 15, TimeUnit.SECONDS);
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
        try {
            if (connection.getResponseCode() != 200) throw new IOException("Photo download failed");
            try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(partial)) {
                copy(input, output, 10 * 1024 * 1024, deadline);
                output.getFD().sync();
            }
            if (partial.length() == 0 || !partial.renameTo(target)) throw new IOException("Photo publication failed");
            return target.getAbsolutePath();
        } finally { timeout.cancel(false); connection.disconnect(); active = null; if (partial.exists()) partial.delete(); }
    }

    private HttpURLConnection open(URI uri, String method) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) uri.toURL().openConnection();
        active = connection;
        connection.setRequestMethod(method);
        connection.setConnectTimeout(5000); connection.setReadTimeout(10000); connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("X-Device-SN", config.deviceSn);
        if (config.credential != null) connection.setRequestProperty("Authorization", "Bearer " + config.credential);
        return connection;
    }

    private static void copy(InputStream input, OutputStream output, int maxBytes, long deadline) throws IOException {
        byte[] buffer = new byte[8192];
        int total = 0, size;
        while ((size = input.read(buffer)) != -1) {
            total += size;
            if (total > maxBytes || System.nanoTime() > deadline || Thread.currentThread().isInterrupted()) {
                throw new IOException("Transfer exceeded resource limit");
            }
            output.write(buffer, 0, size);
        }
        if (System.nanoTime() > deadline) throw new IOException("Transfer deadline exceeded");
    }
    void cancel() { HttpURLConnection current = active; if (current != null) current.disconnect(); }
    @Override public void close() { cancel(); deadlines.shutdownNow(); }
}
