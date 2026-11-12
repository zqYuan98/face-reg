package com.bonuos.face.util;

import com.bonuos.face.exception.ApiException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;

/** Bounded extraction calls. Deadlines cancel the exchange itself, including a trickling body. */
@Component
public class FaceFeatureExtractorClient implements AutoCloseable {
    private static final int FEATURE_DIMENSION = 1024;
    private final URI extractUri;
    private final URI extractV2Uri;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final Semaphore permits;
    private final long readTimeoutNanos;
    private final long totalTimeoutNanos;
    private final int maxImageBytes;
    private final int maxResponseBytes;
    private final long queueWaitNanos;
    private final ScheduledThreadPoolExecutor watchdog;

    /** Refuses at once when all permits are taken. */
    public FaceFeatureExtractorClient(String apiUrl, long connectTimeoutMs, long readTimeoutMs, long totalTimeoutMs,
            int maxConcurrent, int maxImageBytes, int maxResponseBytes) {
        this(apiUrl, connectTimeoutMs, readTimeoutMs, totalTimeoutMs, maxConcurrent, maxImageBytes, maxResponseBytes, 0);
    }

    /** {@code queueWaitMs}: how long a caller may wait for a permit before ALGORITHM_BUSY. */
    @org.springframework.beans.factory.annotation.Autowired
    public FaceFeatureExtractorClient(
            @Value("${face.extractor.api.url:http://localhost:8000}") String apiUrl,
            @Value("${face.extractor.connect-timeout-ms:2000}") long connectTimeoutMs,
            @Value("${face.extractor.read-timeout-ms:10000}") long readTimeoutMs,
            @Value("${face.extractor.total-timeout-ms:15000}") long totalTimeoutMs,
            @Value("${face.extractor.max-concurrent:2}") int maxConcurrent,
            @Value("${face.extractor.max-image-bytes:10485760}") int maxImageBytes,
            @Value("${face.extractor.max-response-bytes:131072}") int maxResponseBytes,
            @Value("${face.extractor.queue-wait-ms:3000}") long queueWaitMs) {
        if (queueWaitMs < 0 || queueWaitMs > 60_000) throw new IllegalArgumentException("Invalid bounded extractor configuration");
        if (connectTimeoutMs < 1 || readTimeoutMs < 1 || totalTimeoutMs < 1 ||
                connectTimeoutMs > 300_000 || readTimeoutMs > 300_000 || totalTimeoutMs > 300_000 ||
                maxConcurrent < 1 || maxConcurrent > 32 || maxImageBytes < 1 || maxImageBytes > 20 * 1024 * 1024 ||
                maxResponseBytes < 1024 || maxResponseBytes > 1024 * 1024) {
            throw new IllegalArgumentException("Invalid bounded extractor configuration");
        }
        extractUri = URI.create(apiUrl.replaceAll("/+$", "") + "/api/extract_feature");
        extractV2Uri = URI.create(apiUrl.replaceAll("/+$", "") + "/api/v2/extract_feature");
        if (!("http".equals(extractUri.getScheme()) || "https".equals(extractUri.getScheme())) ||
                extractUri.getHost() == null || extractUri.getUserInfo() != null) {
            throw new IllegalArgumentException("Extractor URL must be an HTTP(S) server URL");
        }
        httpClient = HttpClient.newBuilder().connectTimeout(Duration.ofMillis(connectTimeoutMs))
                .version(HttpClient.Version.HTTP_1_1).followRedirects(HttpClient.Redirect.NEVER).build();
        permits = new Semaphore(maxConcurrent);
        readTimeoutNanos = TimeUnit.MILLISECONDS.toNanos(readTimeoutMs);
        totalTimeoutNanos = TimeUnit.MILLISECONDS.toNanos(totalTimeoutMs);
        this.maxImageBytes = maxImageBytes;
        this.maxResponseBytes = maxResponseBytes;
        queueWaitNanos = TimeUnit.MILLISECONDS.toNanos(queueWaitMs);
        watchdog = new ScheduledThreadPoolExecutor(1, task -> {
            Thread thread = new Thread(task, "face-extractor-deadlines");
            thread.setDaemon(true);
            return thread;
        });
        watchdog.setRemoveOnCancelPolicy(true);
    }

    public List<Float> extractFeature(File imageFile) throws IOException {
        acquire();
        try {
            checkImageSize(Files.size(imageFile.toPath()));
            return sendRequest(HttpRequest.BodyPublishers.ofFile(imageFile.toPath()));
        } finally {
            permits.release();
        }
    }

    public List<Float> extractFeature(MultipartFile imageFile) throws IOException {
        acquire();
        try {
            checkImageSize(imageFile.getSize());
            // Admit before copying: rejected requests do not retain additional image copies.
            byte[] bytes;
            try (var input = imageFile.getInputStream()) {
                bytes = input.readNBytes(maxImageBytes + 1);
            }
            checkImageSize(bytes.length);
            return sendRequest(HttpRequest.BodyPublishers.ofByteArray(bytes));
        } finally {
            permits.release();
        }
    }

    /**
     * Unit-scale feature of {@code featureVersion} for a stored image. recognizer_v1_1024 uses the
     * original endpoint; arcface_r50_512 the v2 endpoint, whose quality refusals carry a reason.
     */
    public float[] extractFeature(File imageFile, String featureVersion) throws IOException {
        if (FeatureVersions.V1.equals(featureVersion)) {
            List<Float> values = extractFeature(imageFile);
            float[] out = new float[values.size()];
            for (int i = 0; i < out.length; i++) out[i] = values.get(i);
            return out;
        }
        if (!FeatureVersions.V2.equals(featureVersion)) throw new IllegalArgumentException("Unsupported feature version " + featureVersion);
        acquire();
        try {
            checkImageSize(Files.size(imageFile.toPath()));
            return sendRequest(extractV2Uri, HttpRequest.BodyPublishers.ofFile(imageFile.toPath()),
                    response -> parseFeature(response, FeatureVersions.dimension(featureVersion), true));
        } finally {
            permits.release();
        }
    }

    private void acquire() {
        boolean admitted;
        try {
            admitted = permits.tryAcquire(queueWaitNanos, TimeUnit.NANOSECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            admitted = false;
        }
        if (!admitted) {
            throw new ApiException(HttpStatus.TOO_MANY_REQUESTS, "ALGORITHM_BUSY", "特征提取繁忙，请稍后重试");
        }
    }

    private void checkImageSize(long size) {
        if (size < 1) throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_IMAGE", "图片不能为空");
        if (size > maxImageBytes) throw new ApiException(HttpStatus.PAYLOAD_TOO_LARGE, "IMAGE_TOO_LARGE", "图片超过大小限制");
    }

    private List<Float> sendRequest(HttpRequest.BodyPublisher image) {
        float[] values = sendRequest(extractUri, image, response -> parseFeature(response, FEATURE_DIMENSION, false));
        List<Float> result = new ArrayList<>(values.length);
        for (float value : values) result.add(value);
        return result;
    }

    private <T> T sendRequest(URI uri, HttpRequest.BodyPublisher image, java.util.function.Function<HttpResponse<byte[]>, T> parser) {
        String boundary = "face-" + UUID.randomUUID();
        byte[] prefix = ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"upload.jpg\"\r\n" +
                "Content-Type: application/octet-stream\r\n\r\n").getBytes(StandardCharsets.UTF_8);
        byte[] suffix = ("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8);
        HttpRequest request = HttpRequest.newBuilder(uri)
                .timeout(Duration.ofNanos(totalTimeoutNanos))
                .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                .POST(HttpRequest.BodyPublishers.concat(HttpRequest.BodyPublishers.ofByteArray(prefix), image,
                        HttpRequest.BodyPublishers.ofByteArray(suffix))).build();
        Exchange exchange = new Exchange();
        long interval = Math.max(1, Math.min(100, TimeUnit.NANOSECONDS.toMillis(Math.min(readTimeoutNanos, totalTimeoutNanos)) / 10));
        ScheduledFuture<?> timer = watchdog.scheduleAtFixedRate(exchange::checkDeadline, interval, interval, TimeUnit.MILLISECONDS);
        try {
            CompletableFuture<HttpResponse<byte[]>> future = httpClient.sendAsync(request, info -> {
                exchange.lastProgress = System.nanoTime();
                return exchange.body;
            });
            exchange.attach(future);
            HttpResponse<byte[]> response = future.get();
            if (exchange.failure.get() != null) throw exchange.failure.get();
            return parser.apply(response);
        } catch (InterruptedException e) {
            exchange.abort(new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "ALGORITHM_CANCELLED", "特征提取已取消"));
            Thread.currentThread().interrupt();
            throw exchange.failure.get();
        } catch (ExecutionException | CancellationException e) {
            if (exchange.failure.get() != null) throw exchange.failure.get();
            Throwable cause = e instanceof ExecutionException ? e.getCause() : e;
            if (cause instanceof HttpTimeoutException) throw timeout();
            if (cause instanceof ApiException api) throw api;
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "ALGORITHM_UNAVAILABLE", "特征提取服务不可用，请稍后重试");
        } finally {
            timer.cancel(false);
        }
    }

    private float[] parseFeature(HttpResponse<byte[]> response, int dimension, boolean reasons) {
        if (response.statusCode() == 429 || response.statusCode() == 503) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "ALGORITHM_UNAVAILABLE", "特征提取服务繁忙或未就绪");
        }
        if (response.statusCode() == 504) throw timeout();
        if (response.statusCode() == 413) {
            throw new ApiException(HttpStatus.PAYLOAD_TOO_LARGE, "IMAGE_TOO_LARGE", "图片文件或像素尺寸超过限制");
        }
        if (response.statusCode() == 404) {
            // The algorithm service does not load this feature version (FACE_FEATURE_VERSIONS), a deployment mismatch.
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "FEATURE_VERSION_DISABLED", "算法服务未启用该特征版本，请检查部署配置");
        }
        if (response.statusCode() == 400 || response.statusCode() == 422) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "INVALID_IMAGE", "图片无效或没有符合质量要求的人脸");
        }
        if (response.statusCode() < 200 || response.statusCode() >= 300) throw invalidResponse();
        try {
            JsonNode root = objectMapper.readTree(response.body());
            if (root == null || !root.path("success").isBoolean()) throw invalidResponse();
            if (!root.path("success").asBoolean()) {
                String reason = reasons ? root.path("reason_code").asText("") : "";
                throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "NO_USABLE_FACE", REFUSALS.getOrDefault(reason, "未检测到符合质量要求的人脸"));
            }
            JsonNode feature = root.path("feature");
            if (!feature.isArray() || feature.size() != dimension) throw invalidResponse();
            float[] result = new float[dimension];
            double norm = 0;
            int i = 0;
            for (JsonNode value : feature) {
                float number = (float) value.asDouble(Double.NaN);
                if (!value.isNumber() || !Float.isFinite(number)) throw invalidResponse();
                result[i++] = number;
                norm += (double) number * number;
            }
            if (!(norm > 0) || !Double.isFinite(norm)) throw invalidResponse();
            return result;
        } catch (IOException e) {
            throw invalidResponse();
        }
    }

    private static final java.util.Map<String, String> REFUSALS = java.util.Map.of(
            "NO_FACE", "未检测到人脸",
            "FACE_TOO_SMALL", "人脸过小，请靠近镜头或使用更清晰的照片",
            "BAD_POSE", "人脸角度过大，请正对镜头",
            "TOO_DARK", "光线过暗",
            "TOO_BRIGHT", "光线过亮",
            "TOO_BLURRY", "照片模糊",
            "FEATURE_FAILED", "人脸特征提取失败");

    private static ApiException timeout() {
        return new ApiException(HttpStatus.GATEWAY_TIMEOUT, "ALGORITHM_TIMEOUT", "特征提取超时，操作未完成");
    }
    private static ApiException invalidResponse() {
        return new ApiException(HttpStatus.BAD_GATEWAY, "ALGORITHM_INVALID_RESPONSE", "特征提取服务返回无效结果");
    }

    private final class Exchange {
        private final long started = System.nanoTime();
        private volatile long lastProgress = started;
        private final AtomicReference<ApiException> failure = new AtomicReference<>();
        private volatile CompletableFuture<?> requestFuture;
        private final BoundedBody body = new BoundedBody(this);
        private void attach(CompletableFuture<?> future) {
            requestFuture = future;
            if (failure.get() != null) future.cancel(true);
        }
        private void checkDeadline() {
            long now = System.nanoTime();
            if (now - started >= totalTimeoutNanos || now - lastProgress >= readTimeoutNanos) abort(timeout());
        }
        private void abort(ApiException error) {
            if (failure.compareAndSet(null, error)) {
                // JDK 17 sendAsync cancellation terminates the exchange, not only the caller's wait.
                // Cancelling the response subscription also aborts a body still streaming.
                body.cancel(error);
                CompletableFuture<?> future = requestFuture;
                if (future != null) future.cancel(true);
            }
        }
    }

    private final class BoundedBody implements HttpResponse.BodySubscriber<byte[]> {
        private final Exchange exchange;
        private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        private final CompletableFuture<byte[]> result = new CompletableFuture<>();
        private volatile Flow.Subscription subscription;
        private BoundedBody(Exchange exchange) { this.exchange = exchange; }
        @Override public CompletionStage<byte[]> getBody() { return result; }
        @Override public void onSubscribe(Flow.Subscription subscription) {
            this.subscription = subscription;
            if (exchange.failure.get() != null) subscription.cancel();
            else subscription.request(1);
        }
        @Override public void onNext(List<ByteBuffer> buffers) {
            if (exchange.failure.get() != null) return;
            exchange.lastProgress = System.nanoTime();
            exchange.checkDeadline();
            if (exchange.failure.get() != null) return;
            for (ByteBuffer buffer : buffers) {
                if (buffer.remaining() > maxResponseBytes - bytes.size()) {
                    exchange.abort(invalidResponse());
                    return;
                }
                byte[] chunk = new byte[buffer.remaining()];
                buffer.get(chunk);
                bytes.writeBytes(chunk);
            }
            subscription.request(1);
        }
        @Override public void onError(Throwable error) { result.completeExceptionally(error); }
        @Override public void onComplete() { result.complete(bytes.toByteArray()); }
        private void cancel(ApiException error) {
            Flow.Subscription current = subscription;
            if (current != null) current.cancel();
            result.completeExceptionally(error);
        }
    }

    @Override @PreDestroy public void close() { watchdog.shutdownNow(); }
}
