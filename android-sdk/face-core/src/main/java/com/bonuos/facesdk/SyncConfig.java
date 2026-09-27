package com.bonuos.facesdk;

import java.net.URI;
import java.net.URISyntaxException;

/** Provision these settings from the host application's secure device enrollment flow. */
public final class SyncConfig {
    public enum Protocol { V2, LEGACY_FULL }
    public final URI baseUri;
    public final String deviceSn;
    final String credential;
    public final Protocol protocol;

    public SyncConfig(String baseUrl, String deviceSn, String deviceSecret) {
        this(baseUrl, deviceSn, deviceSecret, Protocol.V2, false);
    }

    public SyncConfig(String baseUrl, String deviceSn, String deviceSecret, Protocol protocol, boolean allowCleartext) {
        URI uri = URI.create(baseUrl.endsWith("/") ? baseUrl : baseUrl + "/");
        if ((!"https".equals(uri.getScheme()) && !(allowCleartext && "http".equals(uri.getScheme()))) ||
                uri.getHost() == null || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null ||
                !"/".equals(uri.getPath())) {
            throw new IllegalArgumentException("Configure an HTTPS server origin; cleartext requires explicit opt-in");
        }
        if (deviceSn == null || !deviceSn.matches("[A-Za-z0-9_-]{1,64}")) {
            throw new IllegalArgumentException("Invalid device SN");
        }
        if (protocol == null || (protocol == Protocol.V2 &&
                (deviceSecret == null || deviceSecret.isEmpty() || deviceSecret.contains("\r") || deviceSecret.contains("\n")))) {
            throw new IllegalArgumentException("V2 requires a provisioned device credential");
        }
        this.baseUri = uri;
        this.deviceSn = deviceSn;
        this.credential = deviceSecret == null || deviceSecret.isEmpty() ? null : deviceSn + "." + deviceSecret;
        this.protocol = protocol;
    }

    URI photoUri(String path) {
        if (path == null || path.isEmpty()) throw new IllegalArgumentException("Empty photo path");
        String filename;
        if (path.startsWith("/") || path.startsWith("uploads/") || path.contains(":")) {
            // URL forms must already be URI-encoded; only raw basenames are encoded below.
            URI uri = baseUri.resolve(URI.create(path));
            if (!baseUri.getScheme().equals(uri.getScheme()) || !baseUri.getHost().equalsIgnoreCase(uri.getHost()) ||
                    baseUri.getPort() != uri.getPort() || uri.getUserInfo() != null || uri.getQuery() != null ||
                    uri.getFragment() != null || uri.getPath() == null || !uri.getPath().startsWith("/uploads/")) {
                throw new IllegalArgumentException("Photo URL must use the configured server upload route");
            }
            // URI path decoding keeps '+' as a literal plus, unlike form URL decoding.
            filename = uri.getPath().substring("/uploads/".length());
        } else {
            filename = path;
        }
        if (filename.isEmpty() || filename.length() > 255 || filename.startsWith(".") ||
                !filename.matches("(?i).+\\.(jpg|jpeg|png|webp|bmp|gif)")) {
            throw new IllegalArgumentException("Unsupported photo filename");
        }
        for (int i = 0; i < filename.length(); i++) {
            char character = filename.charAt(i);
            if (Character.isISOControl(character) || "/\\:%<>\"|?*#".indexOf(character) >= 0) {
                // Residual '%' is intentionally rejected: backend resources must not decode twice.
                throw new IllegalArgumentException("Photo filename requires controlled migration");
            }
        }
        try {
            URI canonical = new URI(baseUri.getScheme(), null, baseUri.getHost(), baseUri.getPort(),
                    "/uploads/" + filename, null, null);
            return URI.create(canonical.toASCIIString());
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("Invalid photo filename", error);
        }
    }
}
