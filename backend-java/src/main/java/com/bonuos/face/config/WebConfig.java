package com.bonuos.face.config;

import org.springframework.context.annotation.Configuration;
import java.nio.file.Path;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {

    @org.springframework.beans.factory.annotation.Value("${spring.servlet.multipart.location:./uploads}")
    private String uploadPath;

    @Override
    public void addResourceHandlers(
            org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry registry) {
        // Same-origin UI only; authentication and a direct-child path guard protect this mapping.
        String path = Path.of(uploadPath).toAbsolutePath().normalize().toUri().toString();
        registry.addResourceHandler("/uploads/**")
                .addResourceLocations(path.endsWith("/") ? path : path + "/")
                .setCachePeriod(0);
    }
}
