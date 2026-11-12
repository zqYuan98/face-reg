package com.bonuos.face.util;

import com.bonuos.face.exception.ApiException;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.*;

class FaceFeatureExtractorClientTest {
    private HttpServer server;
    private ExecutorService serverThreads;
    private final List<FaceFeatureExtractorClient> clients = new ArrayList<>();
    private static final MockMultipartFile IMAGE = new MockMultipartFile("image", "x.jpg", "image/jpeg", new byte[]{1, 2, 3});
    private static final String VALID = "{\"success\":true,\"feature\":[" + "0.03125,".repeat(1023) + "0.03125]}";

    @BeforeEach void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        serverThreads = Executors.newCachedThreadPool();
        server.setExecutor(serverThreads);
        server.start();
    }
    @AfterEach void stop() {
        clients.forEach(FaceFeatureExtractorClient::close);
        server.stop(0);
        serverThreads.shutdownNow();
    }
    private FaceFeatureExtractorClient client(long idle, long total, int limit) {
        var client = new FaceFeatureExtractorClient("http://127.0.0.1:" + server.getAddress().getPort(),
                500, idle, total, 1, 1024, limit);
        clients.add(client);
        return client;
    }
    private static void reply(HttpExchange exchange, int status, String body) throws IOException {
        exchange.getRequestBody().readAllBytes();
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status, bytes.length);
        try (var out = exchange.getResponseBody()) { out.write(bytes); }
    }

    @Test void extractsExactly1024FiniteValues() throws IOException {
        server.createContext("/api/extract_feature", exchange -> reply(exchange, 200, VALID));
        assertThat(client(1000, 2000, 32768).extractFeature(IMAGE)).hasSize(1024).containsOnly(0.03125f);
    }

    @Test void v2UsesItsEndpointAndReportsQualityReasons() throws IOException {
        StringBuilder v2 = new StringBuilder("{\"success\":true,\"feature_version\":\"arcface_r50_512\",\"feature\":[");
        for (int i = 0; i < 512; i++) v2.append(i == 0 ? "" : ",").append("0.5");
        v2.append("]}");
        java.util.concurrent.atomic.AtomicBoolean refuse = new java.util.concurrent.atomic.AtomicBoolean();
        server.createContext("/api/v2/extract_feature", exchange -> reply(exchange, 200,
                refuse.get() ? "{\"success\":false,\"reason_code\":\"BAD_POSE\",\"feature\":null}" : v2.toString()));
        server.createContext("/api/extract_feature", exchange -> reply(exchange, 200, VALID));
        var client = client(1000, 2000, 32768);
        java.io.File image = java.nio.file.Files.write(java.nio.file.Files.createTempFile("face", ".jpg"), new byte[]{1, 2, 3}).toFile();
        try {
            assertThat(client.extractFeature(image, "arcface_r50_512")).hasSize(512).containsOnly(0.5f);
            assertThat(client.extractFeature(image, "recognizer_v1_1024")).hasSize(1024);
            refuse.set(true);
            assertThatThrownBy(() -> client.extractFeature(image, "arcface_r50_512")).isInstanceOfSatisfying(ApiException.class, error -> {
                assertThat(error.getErrorCode()).isEqualTo("NO_USABLE_FACE");
                assertThat(error.getMessage()).contains("角度");
            });
        } finally { image.delete(); }
    }

    @Test void disabledVersionIsADeploymentError() throws IOException {
        server.createContext("/api/v2/extract_feature", exchange -> reply(exchange, 404, "{\"detail\":{\"errorCode\":\"FEATURE_VERSION_DISABLED\"}}"));
        java.io.File image = java.nio.file.Files.write(java.nio.file.Files.createTempFile("face", ".jpg"), new byte[]{1, 2, 3}).toFile();
        try {
            assertThatThrownBy(() -> client(1000, 2000, 32768).extractFeature(image, "arcface_r50_512")).isInstanceOfSatisfying(ApiException.class,
                    error -> assertThat(error.getErrorCode()).isEqualTo("FEATURE_VERSION_DISABLED"));
            server.removeContext("/api/v2/extract_feature");
            server.createContext("/api/v2/extract_feature", exchange -> reply(exchange, 413, "{\"detail\":{\"errorCode\":\"IMAGE_DIMENSIONS_TOO_LARGE\"}}"));
            assertThatThrownBy(() -> client(1000, 2000, 32768).extractFeature(image, "arcface_r50_512")).isInstanceOfSatisfying(ApiException.class,
                    error -> assertThat(error.getErrorCode()).isEqualTo("IMAGE_TOO_LARGE"));
        } finally { image.delete(); }
    }

    @Test void queuedCallerRunsWhenThePermitFrees() throws Exception {
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        java.util.concurrent.atomic.AtomicInteger calls = new java.util.concurrent.atomic.AtomicInteger();
        server.createContext("/api/extract_feature", exchange -> {
            if (calls.incrementAndGet() == 1) {
                entered.countDown();
                try { release.await(3, TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            }
            reply(exchange, 200, VALID);
        });
        var client = new FaceFeatureExtractorClient("http://127.0.0.1:" + server.getAddress().getPort(),
                500, 2000, 3000, 1, 1024, 32768, 2000);
        clients.add(client);
        ExecutorService caller = Executors.newFixedThreadPool(2);
        try {
            Future<?> first = caller.submit(() -> { try { return client.extractFeature(IMAGE); } catch (IOException e) { throw new RuntimeException(e); } });
            assertThat(entered.await(1, TimeUnit.SECONDS)).isTrue();
            Future<java.util.List<Float>> second = caller.submit(() -> client.extractFeature(IMAGE));
            Thread.sleep(100);
            assertThat(second.isDone()).isFalse();
            release.countDown();
            first.get(3, TimeUnit.SECONDS);
            assertThat(second.get(3, TimeUnit.SECONDS)).hasSize(1024);
        } finally { release.countDown(); caller.shutdownNow(); }
    }

    @Test void rejectsBusyImmediatelyAndReleasesAfterCompletion() throws Exception {
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        server.createContext("/api/extract_feature", exchange -> {
            entered.countDown();
            try { release.await(3, TimeUnit.SECONDS); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            reply(exchange, 200, VALID);
        });
        var client = client(2000, 3000, 32768);
        ExecutorService caller = Executors.newSingleThreadExecutor();
        try {
            Future<?> first = caller.submit(() -> { try { return client.extractFeature(IMAGE); } catch (IOException e) { throw new RuntimeException(e); } });
            assertThat(entered.await(1, TimeUnit.SECONDS)).isTrue();
            long started = System.nanoTime();
            assertThatThrownBy(() -> client.extractFeature(IMAGE)).isInstanceOfSatisfying(ApiException.class,
                    error -> assertThat(error.getErrorCode()).isEqualTo("ALGORITHM_BUSY"));
            assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)).isLessThan(300);
            release.countDown();
            first.get(3, TimeUnit.SECONDS);
            assertThat(client.extractFeature(IMAGE)).hasSize(1024);
        } finally { release.countDown(); caller.shutdownNow(); }
    }

    @Test void totalDeadlineAbortsTricklingSocketAndNextCallCanProceed() throws Exception {
        CountDownLatch disconnected = new CountDownLatch(1);
        AtomicInteger calls = new AtomicInteger();
        server.createContext("/api/extract_feature", exchange -> {
            if (calls.incrementAndGet() > 1) { reply(exchange, 200, VALID); return; }
            exchange.getRequestBody().readAllBytes();
            exchange.sendResponseHeaders(200, 0);
            try (var out = exchange.getResponseBody()) {
                for (int i = 0; i < 200; i++) {
                    out.write(' '); out.flush();
                    try { Thread.sleep(20); } catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
                }
            } catch (IOException aborted) { disconnected.countDown(); }
        });
        var client = client(150, 350, 32768);
        long started = System.nanoTime();
        assertThatThrownBy(() -> client.extractFeature(IMAGE)).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getErrorCode()).isEqualTo("ALGORITHM_TIMEOUT"));
        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)).isLessThan(1200);
        assertThat(disconnected.await(2, TimeUnit.SECONDS)).as("server observes connection close, not just caller timeout").isTrue();
        assertThat(client.extractFeature(IMAGE)).hasSize(1024);
    }

    @Test void noHeadersHitsReadDeadline() {
        server.createContext("/api/extract_feature", exchange -> {
            try { Thread.sleep(1500); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
            exchange.close();
        });
        long started = System.nanoTime();
        assertThatThrownBy(() -> client(120, 2000, 32768).extractFeature(IMAGE))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getErrorCode()).isEqualTo("ALGORITHM_TIMEOUT"));
        assertThat(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)).isLessThan(1000);
    }

    @Test void interruptedCallerAbortsSocket() throws Exception {
        CountDownLatch entered = new CountDownLatch(1), disconnected = new CountDownLatch(1);
        server.createContext("/api/extract_feature", exchange -> {
            exchange.getRequestBody().readAllBytes();
            exchange.sendResponseHeaders(200, 0);
            entered.countDown();
            try (var out = exchange.getResponseBody()) {
                for (int i = 0; i < 200; i++) {
                    out.write(' '); out.flush();
                    try { Thread.sleep(20); } catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
                }
            } catch (IOException e) { disconnected.countDown(); }
        });
        var client = client(1000, 5000, 32768);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Thread caller = new Thread(() -> { try { client.extractFeature(IMAGE); } catch (Throwable e) { failure.set(e); } });
        caller.start();
        assertThat(entered.await(1, TimeUnit.SECONDS)).isTrue();
        caller.interrupt(); caller.join(1000);
        assertThat(caller.isAlive()).isFalse();
        assertThat(failure.get()).isInstanceOfSatisfying(ApiException.class,
                error -> assertThat(error.getErrorCode()).isEqualTo("ALGORITHM_CANCELLED"));
        assertThat(disconnected.await(2, TimeUnit.SECONDS)).isTrue();
    }

    @Test void capsResponseBeforeParsing() {
        server.createContext("/api/extract_feature", exchange -> reply(exchange, 200, " ".repeat(50000)));
        assertThatThrownBy(() -> client(1000, 2000, 1024).extractFeature(IMAGE))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getErrorCode()).isEqualTo("ALGORITHM_INVALID_RESPONSE"));
    }

    @Test void rejectsWrongDimensionAndNonNumericVector() {
        server.createContext("/api/extract_feature", exchange -> reply(exchange, 200, "{\"success\":true,\"feature\":[1,2]}"));
        assertThatThrownBy(() -> client(1000, 2000, 32768).extractFeature(IMAGE))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.getErrorCode()).isEqualTo("ALGORITHM_INVALID_RESPONSE"));
    }

    @Test void noFaceIsNotReportedAsDependencyFailure() {
        server.createContext("/api/extract_feature", exchange -> reply(exchange, 200, "{\"success\":false,\"message\":\"internal details\"}"));
        assertThatThrownBy(() -> client(1000, 2000, 32768).extractFeature(IMAGE))
                .isInstanceOfSatisfying(ApiException.class, error -> {
                    assertThat(error.getErrorCode()).isEqualTo("NO_USABLE_FACE");
                    assertThat(error.getMessage()).doesNotContain("internal details");
                });
    }
}
