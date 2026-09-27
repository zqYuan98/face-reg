package com.bonuos.face.security;

import com.bonuos.face.exception.ApiException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Component;

@Component
public class AdminAuthentication implements org.springframework.security.authentication.AuthenticationProvider {
    private static final long WINDOW_MS = 300_000;
    private record Attempts(long start, int count) { }
    private final String username;
    private final String passwordHash;
    private final BCryptPasswordEncoder encoder = new BCryptPasswordEncoder();
    private final Semaphore slots = new Semaphore(2);
    private final Map<String, Attempts> attempts = new ConcurrentHashMap<>();

    public AdminAuthentication(@Value("${face.security.admin.username:${FACE_ADMIN_USERNAME:admin}}") String username,
            @Value("${face.security.admin.password-hash:${FACE_ADMIN_PASSWORD_HASH:}}") String hash,
            Environment environment) {
        this.username = username;
        this.passwordHash = hash;
        if (hash.isBlank()) {
            if (environment.acceptsProfiles(Profiles.of("prod", "production")))
                throw new IllegalStateException("FACE_ADMIN_PASSWORD_HASH is required in production");
        } else if (!hash.matches("\\$2[aby]\\$(0[4-9]|1[0-6])\\$[./A-Za-z0-9]{53}")) {
            throw new IllegalStateException("Admin password must be a BCrypt hash with cost 4-16");
        }
    }

    public Authentication authenticate(String suppliedUsername, String password, String remoteAddress) {
        if (passwordHash.isBlank()) throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "ADMIN_NOT_CONFIGURED", "管理员密码尚未配置");
        if (suppliedUsername == null || suppliedUsername.length() > 100 || password == null
                || password.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 72 || password.isEmpty())
            throw new ApiException(HttpStatus.UNAUTHORIZED, "BAD_CREDENTIALS", "用户名或密码错误");
        consumeAttempt(remoteAddress);
        if (!slots.tryAcquire()) throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "LOGIN_BUSY", "登录请求繁忙，请稍后重试");
        try {
            boolean validPassword = encoder.matches(password, passwordHash);
            if (!validPassword || !username.equals(suppliedUsername))
                throw new ApiException(HttpStatus.UNAUTHORIZED, "BAD_CREDENTIALS", "用户名或密码错误");
            attempts.remove(remoteAddress);
            return UsernamePasswordAuthenticationToken.authenticated(username, null, AuthorityUtils.createAuthorityList("ROLE_ADMIN"));
        } finally { slots.release(); }
    }

    @Override public Authentication authenticate(Authentication authentication) {
        String remote = authentication.getDetails() instanceof org.springframework.security.web.authentication.WebAuthenticationDetails details
                ? details.getRemoteAddress() : "internal";
        return authenticate(authentication.getName(), String.valueOf(authentication.getCredentials()), remote);
    }
    @Override public boolean supports(Class<?> type) { return UsernamePasswordAuthenticationToken.class.isAssignableFrom(type); }

    private synchronized void consumeAttempt(String address) {
        long now = System.currentTimeMillis();
        attempts.entrySet().removeIf(entry -> now - entry.getValue().start() >= WINDOW_MS);
        Attempts prior = attempts.get(address);
        if ((prior != null && prior.count() >= 5) || (prior == null && attempts.size() >= 4096))
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "LOGIN_RATE_LIMIT", "登录尝试过多，请五分钟后重试");
        attempts.put(address, new Attempts(prior == null ? now : prior.start(), prior == null ? 1 : prior.count() + 1));
    }
}
