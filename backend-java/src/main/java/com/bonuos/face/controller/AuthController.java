package com.bonuos.face.controller;

import com.bonuos.face.security.AdminAuthentication;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.context.SecurityContextRepository;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.security.web.authentication.session.SessionAuthenticationStrategy;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestController
@RequestMapping("/api")
public class AuthController {
    private final AdminAuthentication authentication;
    private final SecurityContextRepository contexts;
    private final SessionAuthenticationStrategy sessions;
    public AuthController(AdminAuthentication authentication, SecurityContextRepository contexts,
                          SessionAuthenticationStrategy sessions) {
        this.authentication = authentication;
        this.contexts = contexts;
        this.sessions = sessions;
    }
    @GetMapping("/csrf")
    public Map<String, Object> csrf(CsrfToken token) {
        return Map.of("code", 200, "msg", "success", "data", Map.of("token", token.getToken(), "headerName", token.getHeaderName()));
    }
    @PostMapping("/login")
    public Map<String, Object> login(@RequestBody Map<String, String> loginData,
                                    HttpServletRequest request, HttpServletResponse response) {
        Authentication authenticated = authentication.authenticate(loginData.get("username"), loginData.get("password"), request.getRemoteAddr());
        sessions.onAuthentication(authenticated, request, response);
        var context = SecurityContextHolder.createEmptyContext();
        context.setAuthentication(authenticated);
        SecurityContextHolder.setContext(context);
        contexts.saveContext(context, request, response);
        return Map.of("code", 200, "msg", "登录成功", "data", Map.of("username", authenticated.getName()));
    }
    @GetMapping("/me")
    public Map<String, Object> me(Authentication authentication) {
        return Map.of("code", 200, "msg", "success", "data", Map.of("username", authentication.getName()));
    }
}
