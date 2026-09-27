package com.bonuos.face.security;

import com.bonuos.face.exception.ApiException;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import static org.junit.jupiter.api.Assertions.*;

class AdminAuthenticationTest {
    @Test void missingHashCannotLoginAndProductionFailsStartup() {
        var authentication = new AdminAuthentication("admin", "", new MockEnvironment());
        assertEquals("ADMIN_NOT_CONFIGURED", assertThrows(ApiException.class,
                () -> authentication.authenticate("admin", "123456", "127.0.0.1")).getErrorCode());
        assertThrows(IllegalStateException.class,
                () -> new AdminAuthentication("admin", "", new MockEnvironment().withProperty("spring.profiles.active", "prod")));
    }
    @Test void repeatedBadLoginsAreBoundedAndCannotGuessThroughDefaultPassword() {
        var authentication = new AdminAuthentication("admin", new BCryptPasswordEncoder(4).encode("synthetic-password"), new MockEnvironment());
        for (int i = 0; i < 5; i++) assertEquals("BAD_CREDENTIALS", assertThrows(ApiException.class,
                () -> authentication.authenticate("admin", "123456", "127.0.0.1")).getErrorCode());
        assertEquals("LOGIN_RATE_LIMIT", assertThrows(ApiException.class,
                () -> authentication.authenticate("admin", "synthetic-password", "127.0.0.1")).getErrorCode());
    }
}
