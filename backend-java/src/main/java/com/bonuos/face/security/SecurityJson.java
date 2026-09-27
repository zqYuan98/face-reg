package com.bonuos.face.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;

public final class SecurityJson {
    private SecurityJson() { }
    public static void error(ObjectMapper mapper, HttpServletResponse response, int status,
                             String errorCode, String message) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json");
        response.setCharacterEncoding("UTF-8");
        mapper.writeValue(response.getOutputStream(), Map.of("code", status, "errorCode", errorCode, "msg", message));
    }
}
