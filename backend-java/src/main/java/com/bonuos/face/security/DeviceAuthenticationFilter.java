package com.bonuos.face.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import java.util.Optional;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

public class DeviceAuthenticationFilter extends OncePerRequestFilter {
    private final DeviceCredentials credentials;
    private final ObjectMapper mapper;
    public DeviceAuthenticationFilter(DeviceCredentials credentials, ObjectMapper mapper) {
        this.credentials = credentials;
        this.mapper = mapper;
    }
    public static boolean isDeviceRoute(HttpServletRequest request) {
        String path = path(request);
        return path.startsWith("/api/sync/") || path.equals("/api/devices/register") || path.equals("/ws/sync");
    }
    private static boolean isLegacyRoute(String path) {
        return path.equals("/api/sync/users") || path.equals("/api/devices/register") || path.equals("/ws/sync")
                || path.startsWith("/uploads/");
    }
    public static String path(HttpServletRequest request) {
        return request.getRequestURI().substring(request.getContextPath().length());
    }
    @Override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String path = path(request);
        String authorization = request.getHeader("Authorization");
        Optional<DevicePrincipal> identity = Optional.empty();
        if (authorization != null) {
            if (authorization.startsWith("Bearer ")) identity = credentials.bearer(authorization.substring(7));
            if (identity.isEmpty()) {
                SecurityJson.error(mapper, response, 401, "DEVICE_UNAUTHORIZED", "设备凭据无效或已撤销");
                return;
            }
        } else if (isLegacyRoute(path)) identity = credentials.legacy(request);

        if (identity.isPresent()) {
            DevicePrincipal device = identity.get();
            String claimedSn = request.getHeader("X-Device-SN");
            String registrationSn = path.equals("/api/devices/register") ? request.getParameter("deviceSn") : null;
            if ((claimedSn != null && !claimedSn.equals(device.deviceSn()))
                    || (registrationSn != null && !registrationSn.equals(device.deviceSn()))) {
                SecurityJson.error(mapper, response, 403, "DEVICE_SCOPE_DENIED", "设备序列号不匹配");
                return;
            }
            if (path.equals("/api/sync/users") && !device.allGroups()) {
                SecurityJson.error(mapper, response, 403, "DEVICE_SCOPE_DENIED", "分组授权设备须使用 v2 同步接口");
                return;
            }
            // The context is request-local; it is deliberately never saved into an admin session.
            var context = SecurityContextHolder.createEmptyContext();
            context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated(device, null,
                    List.of(new SimpleGrantedAuthority("ROLE_DEVICE"))));
            SecurityContextHolder.setContext(context);
        }
        chain.doFilter(request, response);
    }
}
