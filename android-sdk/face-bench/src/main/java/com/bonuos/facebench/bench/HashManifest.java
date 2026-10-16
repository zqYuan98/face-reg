package com.bonuos.facebench.bench;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeSet;

/** Content-only fingerprints; no absolute input paths, face data or credentials in the manifest. */
public final class HashManifest {
    private HashManifest() { }
    public static Map<String, Object> file(File file) throws IOException {
        try (InputStream input = new FileInputStream(file)) { return stream(input); }
    }
    /** Caller owns the stream. */
    public static Map<String, Object> stream(InputStream input) throws IOException {
        MessageDigest digest = digest();
        byte[] buffer = new byte[64 * 1024];
        long size = 0;
        int n;
        while ((n = input.read(buffer)) != -1) { digest.update(buffer, 0, n); size += n; }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("sha256", hex(digest.digest()));
        result.put("bytes", size);
        return result;
    }
    public static String fingerprint(Object value) {
        byte[] bytes = canonical(new Gson().toJsonTree(value)).toString().getBytes(StandardCharsets.UTF_8);
        return hex(digest().digest(bytes));
    }
    private static JsonElement canonical(JsonElement value) {
        if (value.isJsonObject()) {
            JsonObject result = new JsonObject();
            for (String key : new TreeSet<>(value.getAsJsonObject().keySet()))
                result.add(key, canonical(value.getAsJsonObject().get(key)));
            return result;
        }
        if (value.isJsonArray()) {
            JsonArray result = new JsonArray();
            for (JsonElement item : value.getAsJsonArray()) result.add(canonical(item));
            return result;
        }
        return value;
    }
    private static MessageDigest digest() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
    private static String hex(byte[] bytes) {
        StringBuilder text = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) text.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return text.toString();
    }
}
