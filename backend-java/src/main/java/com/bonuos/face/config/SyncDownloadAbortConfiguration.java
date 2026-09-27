package com.bonuos.face.config;

import jakarta.servlet.ServletException;
import org.apache.catalina.connector.Request;
import org.apache.catalina.connector.Response;
import org.apache.catalina.valves.ValveBase;
import org.apache.coyote.ActionCode;
import org.springframework.boot.web.embedded.tomcat.TomcatServletWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.io.IOException;
import java.util.function.Consumer;

/** Tomcat-specific abort: timeout must discard pending bytes, not enter a blocking final flush. */
@Configuration
public class SyncDownloadAbortConfiguration {
    public static final String ABORT_ATTRIBUTE = SyncDownloadAbortConfiguration.class.getName() + ".abort";

    @Bean public WebServerFactoryCustomizer<TomcatServletWebServerFactory> syncDownloadAbortValve() {
        return factory -> factory.addContextValves(new AbortValve());
    }

    public static final class AbortValve extends ValveBase {
        public AbortValve() { super(true); }
        @Override public void invoke(Request request, Response response) throws IOException, ServletException {
            if ((request.getContextPath() + "/api/sync/users").equals(request.getRequestURI())) {
                request.setAttribute(ABORT_ATTRIBUTE, (Consumer<Throwable>) error ->
                        response.getCoyoteResponse().action(ActionCode.CLOSE_NOW, error));
            }
            getNext().invoke(request, response);
        }
    }
}
