package com.bonuos.face.security;

import com.bonuos.face.controller.AuthController;
import com.bonuos.face.handler.GlobalExceptionHandler;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.mock.web.MockHttpSession;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.test.context.ContextConfiguration;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.web.bind.annotation.*;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(controllers = {AuthController.class, SecurityFilterChainTest.ProbeController.class})
@ContextConfiguration(classes = {SecurityConfig.class, AuthController.class, AdminAuthentication.class,
        DeviceCredentials.class, PhotoAccess.class, SecurityFilterChainTest.ProbeController.class, GlobalExceptionHandler.class})
class SecurityFilterChainTest {
    private static final String PASSWORD = "local-test-password";
    private static final String GLOBAL_TOKEN = "global." + "A".repeat(40);
    private static final String SCOPED_TOKEN = "scoped." + "B".repeat(40);
    @Autowired MockMvc mvc;
    @Autowired ObjectMapper mapper;
    @MockBean JdbcTemplate jdbc;

    @DynamicPropertySource static void properties(DynamicPropertyRegistry registry) {
        registry.add("face.security.admin.password-hash", () -> new BCryptPasswordEncoder(4).encode(PASSWORD));
        registry.add("face.security.devices.credentials-json", () -> """
            [{"deviceSn":"global","tokenSha256":"%s","groupIds":[],"allGroups":true,"scopeVersion":1},
             {"deviceSn":"scoped","tokenSha256":"%s","groupIds":[7],"allGroups":false,"scopeVersion":2}]
            """.formatted(hash(GLOBAL_TOKEN), hash(SCOPED_TOKEN)));
        registry.add("face.security.legacy.enabled", () -> "true");
        registry.add("face.security.legacy.expires-at", () -> "2099-01-01T00:00:00Z");
        registry.add("face.security.legacy.networks", () -> "192.0.2.0/24");
        registry.add("face.security.legacy.inventory-json", () -> "[{\"deviceSn\":\"old-01\",\"networks\":[\"192.0.2.10/32\"]}]");
    }
    @BeforeEach void resetWrites() { ProbeController.writes.set(0); }

    @Test void anonymousAdminAndImagesAreDenied() throws Exception {
        for (String path : List.of("/api/users/page", "/api/groups/page", "/api/me", "/uploads/photo.jpg"))
            mvc.perform(get(path)).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.code").value(401));
        verifyNoInteractions(jdbc);
    }
    @Test void publicLoginPageAndAssetsDoNotRequireASession() throws Exception {
        for (String path : List.of("/login.html", "/company_icon.png", "/logo.png"))
            mvc.perform(get(path)).andExpect(status().isOk());
    }
    @Test void realLoginRotatesSessionAndCsrfThenLogoutInvalidatesIt() throws Exception {
        MvcResult bootstrap = mvc.perform(get("/api/csrf")).andExpect(status().isOk()).andReturn();
        MockHttpSession session = (MockHttpSession) bootstrap.getRequest().getSession(false);
        assertNotNull(session);
        String beforeId = session.getId();
        String beforeToken = token(bootstrap);
        mvc.perform(post("/api/login").session(session).header("X-CSRF-TOKEN", beforeToken)
                        .contentType("application/json").content("{\"username\":\"admin\",\"password\":\"" + PASSWORD + "\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.username").value("admin"))
                .andExpect(jsonPath("$.token").doesNotExist());
        assertNotEquals(beforeId, session.getId());
        mvc.perform(get("/api/me").session(session)).andExpect(status().isOk()).andExpect(jsonPath("$.data.username").value("admin"));
        mvc.perform(post("/api/users/deleteBatch").session(session).header("X-CSRF-TOKEN", beforeToken)
                .contentType("application/json").content("[1]"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.errorCode").value("CSRF_INVALID"));
        String currentToken = token(mvc.perform(get("/api/csrf").session(session)).andReturn());
        assertNotEquals(beforeToken, currentToken);
        mvc.perform(post("/api/users/deleteBatch").session(session).header("X-CSRF-TOKEN", currentToken)
                .contentType("application/json").content("[1]"))
                .andExpect(status().isOk());
        mvc.perform(post("/api/logout").session(session).header("X-CSRF-TOKEN", currentToken))
                .andExpect(status().isOk()).andExpect(jsonPath("$.code").value(200));
        assertTrue(session.isInvalid());
        mvc.perform(get("/api/me")).andExpect(status().isUnauthorized());
    }
    @Test void loginRequiresCsrfAndBadPasswordDoesNotAuthenticate() throws Exception {
        mvc.perform(post("/api/login").contentType("application/json").content("{}"))
                .andExpect(status().isForbidden());
        mvc.perform(post("/api/login").with(csrf()).contentType("application/json")
                        .content("{\"username\":\"admin\",\"password\":\"wrong\"}"))
                .andExpect(status().isUnauthorized()).andExpect(jsonPath("$.errorCode").value("BAD_CREDENTIALS"));
        mvc.perform(get("/api/me")).andExpect(status().isUnauthorized());
    }
    @Test void authenticatedWriteWithoutCsrfDoesNotReachController() throws Exception {
        mvc.perform(post("/api/users/deleteBatch").with(user("admin").roles("ADMIN"))
                .contentType("application/json").content("[1]"))
                .andExpect(status().isForbidden());
        assertEquals(0, ProbeController.writes.get());
    }
    @Test void fractionalIdIsRejectedBeforeAnyWrite() throws Exception {
        mvc.perform(post("/api/users/deleteBatch").with(user("admin").roles("ADMIN")).with(csrf())
                .contentType("application/json").content("[1.9]"))
                .andExpect(status().isBadRequest());
        assertEquals(0, ProbeController.writes.get());
    }
    @Test void deviceAndAdminIdentitiesCannotAccessEachOthersRoutes() throws Exception {
        mvc.perform(get("/api/users/page").header("Authorization", "Bearer " + GLOBAL_TOKEN))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/sync/users").with(user("admin").roles("ADMIN")))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/sync/users").header("Authorization", "Bearer " + GLOBAL_TOKEN))
                .andExpect(status().isOk());
        mvc.perform(get("/api/sync/users").header("Authorization", "Bearer " + SCOPED_TOKEN))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.errorCode").value("DEVICE_SCOPE_DENIED"));
        MvcResult device = mvc.perform(get("/api/sync/v2/probe").header("Authorization", "Bearer " + SCOPED_TOKEN))
                .andExpect(status().isOk()).andExpect(jsonPath("$.deviceSn").value("scoped"))
                .andExpect(jsonPath("$.scopeVersion").value(2)).andReturn();
        assertNull(device.getRequest().getSession(false));
    }
    @Test void registrationNeedsMatchingDeviceIdentityButNotAdminCsrf() throws Exception {
        mvc.perform(post("/api/devices/register").header("Authorization", "Bearer " + GLOBAL_TOKEN).param("deviceSn", "global"))
                .andExpect(status().isOk());
        mvc.perform(post("/api/devices/register").header("Authorization", "Bearer " + GLOBAL_TOKEN).param("deviceSn", "other"))
                .andExpect(status().isForbidden());
        mvc.perform(get("/api/sync/users").header("Authorization", "Bearer " + GLOBAL_TOKEN).header("X-Device-SN", "other"))
                .andExpect(status().isForbidden());
    }
    @Test void deviceRequestCannotReplaceAnExistingAdminSession() throws Exception {
        MvcResult bootstrap = mvc.perform(get("/api/csrf")).andReturn();
        MockHttpSession session = (MockHttpSession) bootstrap.getRequest().getSession(false);
        mvc.perform(post("/api/login").session(session).header("X-CSRF-TOKEN", token(bootstrap))
                        .contentType("application/json").content("{\"username\":\"admin\",\"password\":\"" + PASSWORD + "\"}"))
                .andExpect(status().isOk());
        mvc.perform(get("/api/sync/v2/probe").session(session).header("Authorization", "Bearer " + SCOPED_TOKEN))
                .andExpect(status().isOk()).andExpect(jsonPath("$.deviceSn").value("scoped"));
        mvc.perform(get("/api/me").session(session)).andExpect(status().isOk())
                .andExpect(jsonPath("$.data.username").value("admin"));
        mvc.perform(get("/api/sync/v2/probe").session(session)).andExpect(status().isForbidden());
    }
    @Test void revokedOrMalformedTokenNeverFallsBackToLegacy() throws Exception {
        mvc.perform(get("/api/sync/users").header("Authorization", "Bearer revoked." + "A".repeat(40))
                        .with(request -> { request.setRemoteAddr("192.0.2.10"); return request; }))
                .andExpect(status().isUnauthorized());
    }
    @Test void legacyInventoryNetworkOnlyAllowsMinimumOldRoutes() throws Exception {
        mvc.perform(get("/api/sync/users").with(request -> { request.setRemoteAddr("192.0.2.10"); return request; }))
                .andExpect(status().isOk());
        mvc.perform(get("/api/users/page").with(request -> { request.setRemoteAddr("192.0.2.10"); return request; }))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/sync/v2/probe").with(request -> { request.setRemoteAddr("192.0.2.10"); return request; }))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/sync/users").header("X-Forwarded-For", "192.0.2.10")
                        .with(request -> { request.setRemoteAddr("198.51.100.2"); return request; }))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/api/sync/users").with(request -> { request.setRemoteAddr("192.0.2.11"); return request; }))
                .andExpect(status().isUnauthorized());
    }
    @Test void hiddenPhotoDirectoriesAndScopedPhotoLeaksAreDenied() throws Exception {
        for (String path : List.of("/uploads/.staging/new.jpg", "/uploads/.cleanup/file.json", "/uploads/a/file.jpg", "/uploads/test.html"))
            mvc.perform(get(path).with(user("admin").roles("ADMIN"))).andExpect(status().isForbidden());
        mvc.perform(get("/uploads/photo.jpg").header("Authorization", "Bearer " + SCOPED_TOKEN))
                .andExpect(status().isForbidden());
        when(jdbc.query(any(org.springframework.jdbc.core.PreparedStatementCreator.class),
                org.mockito.ArgumentMatchers.<org.springframework.jdbc.core.ResultSetExtractor<Boolean>>any())).thenReturn(true);
        mvc.perform(get("/uploads/photo.jpg").header("Authorization", "Bearer " + SCOPED_TOKEN))
                .andExpect(status().isOk());
    }
    @Test void externalOriginDoesNotReceiveCredentialedCorsHeaders() throws Exception {
        mvc.perform(get("/api/csrf").header("Origin", "https://untrusted.invalid"))
                .andExpect(header().doesNotExist("Access-Control-Allow-Origin"))
                .andExpect(header().doesNotExist("Access-Control-Allow-Credentials"));
    }
    private String token(MvcResult response) throws Exception { return mapper.readTree(response.getResponse().getContentAsString()).path("data").path("token").asText(); }
    private static String hash(String token) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(token.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception ex) { throw new IllegalStateException(ex); }
    }
    @RestController static class ProbeController {
        static final AtomicInteger writes = new AtomicInteger();
        @GetMapping({"/api/users/page", "/api/groups/page", "/api/sync/users", "/login.html", "/company_icon.png", "/logo.png", "/uploads/{name}"})
        Map<String, Object> get() { return Map.of("code", 200); }
        @GetMapping("/api/sync/v2/probe") DevicePrincipal device() { return DevicePrincipal.current(); }
        @PostMapping("/api/users/deleteBatch") Map<String, Object> write(@RequestBody List<Long> ids) { writes.incrementAndGet(); return Map.of("code", 200); }
        @PostMapping("/api/devices/register") Map<String, Object> register() { return Map.of("code", 200); }
    }
}
