package com.bonuos.face.security;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.web.util.matcher.IpAddressMatcher;
import org.springframework.stereotype.Component;

/** Configuration-only credential inventory. Removing a credential and restarting revokes it. */
@Component
public class DeviceCredentials {
    public record Credential(String deviceSn, String tokenSha256, Set<Long> groupIds,
                             boolean allGroups, long scopeVersion) { }
    public record LegacyDevice(String deviceSn, List<String> networks) { }
    private record Entry(byte[] hash, DevicePrincipal principal) { }
    private record LegacyEntry(String deviceSn, List<IpAddressMatcher> networks) { }
    private final Map<String, Entry> credentials;
    private final boolean legacyEnabled;
    private final Instant legacyExpires;
    private final List<IpAddressMatcher> legacyNetworks;
    private final List<LegacyEntry> legacyInventory;

    public DeviceCredentials(ObjectMapper mapper,
            @Value("${face.security.devices.credentials-json:${FACE_DEVICE_CREDENTIALS_JSON:[]}}") String credentialJson,
            @Value("${face.security.legacy.enabled:${FACE_LEGACY_DEVICE_ENABLED:false}}") boolean legacyEnabled,
            @Value("${face.security.legacy.expires-at:${FACE_LEGACY_DEVICE_EXPIRES_AT:}}") String expires,
            @Value("${face.security.legacy.networks:${FACE_LEGACY_DEVICE_NETWORKS:}}") String networks,
            @Value("${face.security.legacy.inventory-json:${FACE_LEGACY_DEVICE_INVENTORY_JSON:[]}}") String inventoryJson) {
        try {
            List<Credential> configured = mapper.readValue(credentialJson, new TypeReference<>() { });
            if (configured == null || configured.size() > 1000) throw new IllegalArgumentException();
            Map<String, Entry> entries = new HashMap<>();
            for (Credential c : configured) {
                if (!validSn(c.deviceSn()) || c.tokenSha256() == null || !c.tokenSha256().matches("[a-fA-F0-9]{64}")
                        || c.scopeVersion() < 1 || c.groupIds() == null || c.groupIds().size() > 100
                        || c.groupIds().stream().anyMatch(id -> id == null || id < 1)
                        || (!c.allGroups() && c.groupIds().isEmpty())) throw new IllegalArgumentException();
                Entry entry = new Entry(HexFormat.of().parseHex(c.tokenSha256()),
                        new DevicePrincipal(c.deviceSn(), c.groupIds(), c.allGroups(), c.scopeVersion(), false));
                if (entries.put(c.deviceSn(), entry) != null) throw new IllegalArgumentException();
            }
            credentials = Map.copyOf(entries);
            legacyExpires = expires.isBlank() ? null : Instant.parse(expires);
            legacyNetworks = parseNetworks(networks.isBlank() ? List.of() : Arrays.asList(networks.split(",")));
            List<LegacyDevice> devices = mapper.readValue(inventoryJson, new TypeReference<>() { });
            if (devices == null || devices.size() > 1000) throw new IllegalArgumentException();
            List<LegacyEntry> inventory = new ArrayList<>();
            Set<String> serials = new HashSet<>();
            for (LegacyDevice d : devices) {
                if (!validSn(d.deviceSn()) || !serials.add(d.deviceSn()) || d.networks() == null || d.networks().isEmpty())
                    throw new IllegalArgumentException();
                for (String network : d.networks()) {
                    String address = network.trim();
                    if (!(address.endsWith("/32") && !address.contains(":"))
                            && !(address.endsWith("/128") && address.contains(":"))) throw new IllegalArgumentException();
                }
                inventory.add(new LegacyEntry(d.deviceSn(), parseNetworks(d.networks())));
            }
            legacyInventory = List.copyOf(inventory);
            this.legacyEnabled = legacyEnabled && legacyExpires != null && !legacyNetworks.isEmpty() && !legacyInventory.isEmpty();
        } catch (Exception ex) {
            throw new IllegalStateException("Invalid device security configuration");
        }
    }

    public Optional<DevicePrincipal> bearer(String token) {
        if (token == null || token.length() > 384) return Optional.empty();
        int separator = token.indexOf('.');
        if (separator < 1) return Optional.empty();
        Entry entry = credentials.get(token.substring(0, separator));
        String secret = token.substring(separator + 1);
        if (entry == null || !secret.matches("[A-Za-z0-9_-]{32,256}")) return Optional.empty();
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(token.getBytes(StandardCharsets.UTF_8));
            return MessageDigest.isEqual(entry.hash(), hash) ? Optional.of(entry.principal()) : Optional.empty();
        } catch (java.security.NoSuchAlgorithmException ex) { throw new IllegalStateException(ex); }
    }

    public Optional<DevicePrincipal> legacy(HttpServletRequest request) {
        if (!legacyEnabled || !Instant.now().isBefore(legacyExpires)) return Optional.empty();
        // Only the TCP peer is authoritative. Forwarded headers are deliberately ignored.
        String remoteAddress = request.getRemoteAddr();
        if (legacyNetworks.stream().noneMatch(n -> n.matches(remoteAddress))) return Optional.empty();
        List<LegacyEntry> matching = legacyInventory.stream()
                .filter(d -> d.networks().stream().anyMatch(n -> n.matches(remoteAddress))).toList();
        if (matching.size() != 1) return Optional.empty();
        return Optional.of(new DevicePrincipal(matching.get(0).deviceSn(), Set.of(), true, 1, true));
    }

    private static List<IpAddressMatcher> parseNetworks(List<String> values) {
        if (values.size() > 100) throw new IllegalArgumentException();
        return values.stream().map(String::trim).map(value -> {
            if (!value.matches("[0-9a-fA-F:.]+/[0-9]{1,3}") || value.endsWith("/0")) throw new IllegalArgumentException();
            return new IpAddressMatcher(value);
        }).toList();
    }
    private static boolean validSn(String value) { return value != null && value.matches("[A-Za-z0-9_-]{1,64}"); }
}
