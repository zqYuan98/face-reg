package com.bonuos.face.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import static org.junit.jupiter.api.Assertions.*;

class DeviceCredentialsTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private static final String INVENTORY = "[{\"deviceSn\":\"old-01\",\"networks\":[\"192.0.2.10/32\"]}]";
    @Test void legacyIsClosedByDefaultAndIfAnyRequiredInputIsMissing() {
        String future = Instant.now().plusSeconds(3600).toString();
        assertClosed(new DeviceCredentials(mapper, "[]", false, future, "192.0.2.0/24", INVENTORY));
        assertClosed(new DeviceCredentials(mapper, "[]", true, "", "192.0.2.0/24", INVENTORY));
        assertClosed(new DeviceCredentials(mapper, "[]", true, future, "", INVENTORY));
        assertClosed(new DeviceCredentials(mapper, "[]", true, future, "192.0.2.0/24", "[]"));
    }
    @Test void expiredWindowAndWrongNetworkFailClosed() {
        assertClosed(new DeviceCredentials(mapper, "[]", true, Instant.now().minusSeconds(1).toString(), "192.0.2.0/24", INVENTORY));
        assertClosed(new DeviceCredentials(mapper, "[]", true, Instant.now().plusSeconds(3600).toString(), "198.51.100.0/24", INVENTORY));
    }
    @Test void ambiguousNatInventoryFailsClosed() {
        String duplicateIp = "[{\"deviceSn\":\"old-01\",\"networks\":[\"192.0.2.10/32\"]},{\"deviceSn\":\"old-02\",\"networks\":[\"192.0.2.10/32\"]}]";
        assertClosed(new DeviceCredentials(mapper, "[]", true, Instant.now().plusSeconds(3600).toString(), "192.0.2.0/24", duplicateIp));
    }
    @Test void unsafeInventoryAndWildcardNetworkAreRejectedAtStartup() {
        assertThrows(IllegalStateException.class, () -> new DeviceCredentials(mapper, "[]", true,
                Instant.now().plusSeconds(3600).toString(), "0.0.0.0/0", INVENTORY));
        assertThrows(IllegalStateException.class, () -> new DeviceCredentials(mapper, "[]", true,
                Instant.now().plusSeconds(3600).toString(), "192.0.2.0/24", INVENTORY.replace("/32", "/24")));
    }
    private void assertClosed(DeviceCredentials credentials) {
        var request = new MockHttpServletRequest();
        request.setRemoteAddr("192.0.2.10");
        assertTrue(credentials.legacy(request).isEmpty());
    }
    @Test void deviceSerialMatchesDatabase64CharacterLimit() {
        String credentials = "[{\"deviceSn\":\"%s\",\"tokenSha256\":\"%s\",\"groupIds\":[],\"allGroups\":true,\"scopeVersion\":1}]";
        assertDoesNotThrow(() -> new DeviceCredentials(mapper,
                credentials.formatted("a".repeat(64), "0".repeat(64)), false, "", "", "[]"));
        assertThrows(IllegalStateException.class, () -> new DeviceCredentials(mapper,
                credentials.formatted("a".repeat(65), "0".repeat(64)), false, "", "", "[]"));
        assertThrows(IllegalStateException.class, () -> new DeviceCredentials(mapper, "[]", true,
                Instant.now().plusSeconds(3600).toString(), "192.0.2.0/24", INVENTORY.replace("old-01", "a".repeat(65))));
    }
}
