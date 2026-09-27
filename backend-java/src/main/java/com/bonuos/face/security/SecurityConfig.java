package com.bonuos.face.security;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.jackson.Jackson2ObjectMapperBuilderCustomizer;
import org.springframework.boot.web.servlet.ServletContextInitializer;
import org.springframework.boot.web.servlet.server.CookieSameSiteSupplier;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authorization.AuthorizationDecision;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.security.web.context.SecurityContextRepository;
import org.springframework.security.web.csrf.CsrfAuthenticationStrategy;
import org.springframework.security.web.csrf.CsrfFilter;
import org.springframework.security.web.csrf.CsrfTokenRepository;
import org.springframework.security.web.csrf.CsrfTokenRequestAttributeHandler;
import org.springframework.security.web.csrf.HttpSessionCsrfTokenRepository;
import org.springframework.security.web.authentication.session.ChangeSessionIdAuthenticationStrategy;
import org.springframework.security.web.authentication.session.CompositeSessionAuthenticationStrategy;
import org.springframework.security.web.authentication.session.SessionAuthenticationStrategy;

@Configuration
public class SecurityConfig {
    @Bean public SecurityContextRepository securityContextRepository() { return new HttpSessionSecurityContextRepository(); }
    @Bean public CsrfTokenRepository csrfTokenRepository() {
        var repository = new HttpSessionCsrfTokenRepository();
        repository.setHeaderName("X-CSRF-TOKEN");
        return repository;
    }
    @Bean public SessionAuthenticationStrategy loginSessionStrategy(CsrfTokenRepository csrf) {
        return new CompositeSessionAuthenticationStrategy(java.util.List.of(
                new ChangeSessionIdAuthenticationStrategy(), new CsrfAuthenticationStrategy(csrf)));
    }
    @Bean public ServletContextInitializer sessionCookieSettings(
            @Value("${face.security.session.secure:${FACE_SESSION_COOKIE_SECURE:true}}") boolean secure) {
        return context -> {
            context.setSessionTimeout(30);
            var cookie = context.getSessionCookieConfig();
            cookie.setName("FACE_SESSION");
            cookie.setHttpOnly(true);
            cookie.setSecure(secure);
            cookie.setPath("/");
        };
    }
    @Bean public CookieSameSiteSupplier sessionSameSite() { return CookieSameSiteSupplier.ofLax().whenHasName("FACE_SESSION"); }
    @Bean public Jackson2ObjectMapperBuilderCustomizer strictIntegerParameters() {
        return builder -> builder.featuresToDisable(DeserializationFeature.ACCEPT_FLOAT_AS_INT);
    }
    @Bean public SecurityFilterChain securityFilterChain(HttpSecurity http, ObjectMapper mapper,
            DeviceCredentials devices, PhotoAccess photos, SecurityContextRepository contexts,
            CsrfTokenRepository csrf) throws Exception {
        http.securityContext(config -> config.securityContextRepository(contexts).requireExplicitSave(true))
                // AuthController alone invokes session fixation/CSRF rotation and saves admin contexts.
                // Automatic SessionManagementFilter would persist a request-only device identity.
                .sessionManagement(config -> config.requireExplicitAuthenticationStrategy(true))
                .requestCache(config -> config.disable())
                .formLogin(config -> config.disable()).httpBasic(config -> config.disable())
                .csrf(config -> config.csrfTokenRepository(csrf)
                        .csrfTokenRequestHandler(new CsrfTokenRequestAttributeHandler())
                        .ignoringRequestMatchers(DeviceAuthenticationFilter::isDeviceRoute))
                .addFilterBefore(new DeviceAuthenticationFilter(devices, mapper), CsrfFilter.class)
                .authorizeHttpRequests(config -> config
                        .requestMatchers("/", "/index.html", "/login.html", "/company_icon.png", "/logo.png", "/favicon.ico",
                                "/api/csrf", "/api/login", "/error").permitAll()
                        .requestMatchers(DeviceAuthenticationFilter::isDeviceRoute).hasRole("DEVICE")
                        .requestMatchers("/uploads/**").access((authentication, context) ->
                                new AuthorizationDecision(photos.allowed(authentication.get(), context.getRequest())))
                        .anyRequest().hasRole("ADMIN"))
                .exceptionHandling(config -> config
                        .authenticationEntryPoint((request, response, error) ->
                                SecurityJson.error(mapper, response, 401, "UNAUTHENTICATED", "请先登录或提供设备凭据"))
                        .accessDeniedHandler((request, response, error) ->
                                SecurityJson.error(mapper, response, 403,
                                        error instanceof org.springframework.security.web.csrf.CsrfException ? "CSRF_INVALID" : "FORBIDDEN",
                                        "请求无权限或安全令牌已失效")))
                .logout(config -> config.logoutUrl("/api/logout").deleteCookies("FACE_SESSION")
                        .invalidateHttpSession(true).clearAuthentication(true)
                        .logoutSuccessHandler((request, response, authentication) -> {
                            response.setContentType("application/json");
                            response.setCharacterEncoding("UTF-8");
                            mapper.writeValue(response.getOutputStream(), Map.of("code", 200, "msg", "已退出登录"));
                        }));
        return http.build();
    }
}
