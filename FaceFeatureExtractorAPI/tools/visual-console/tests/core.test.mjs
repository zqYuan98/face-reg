import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPABILITIES,
  EXPECTED_FEATURE_DIMENSION,
  REASON_LABELS,
  buildResultDetails,
  dataUrlToBlob,
  getCapability,
  getSuggestionView,
  isCapabilityReady,
  safeUploadFilename,
  safeJsonText,
  translateReasons,
  validateSelection,
} from "../core.js";

const MIB = 1024 * 1024;

function media(name, size, type, metadata = {}) {
  return {
    file: { name, size, type },
    ...metadata,
  };
}

test("CAPABILITIES freezes the exact five deployed v2 contracts", () => {
  assert.deepEqual(CAPABILITIES.map(({ id }) => id), [
    "portrait",
    "witness",
    "verify-images",
    "verify-video-quick",
    "verify-video-full",
  ]);
  assert.deepEqual(
    CAPABILITIES.map(({ endpoint }) => endpoint),
    [
      "/api/v2/portrait/generate",
      "/api/v2/witness/blur-background",
      "/api/v2/verify/images",
      "/api/v2/verify/video/quick",
      "/api/v2/verify/video/full",
    ],
  );
  assert.deepEqual(getCapability("verify-images").fields.map(({ name }) => name), [
    "reference_image",
    "probe_image",
  ]);
  assert.deepEqual(getCapability("quick").fields.map(({ name }) => name), [
    "reference_image",
    "video",
  ]);
  assert.equal(getCapability("portrait").limits.maxImageBytes, 15 * MIB);
  assert.equal(getCapability("portrait").limits.maxImagePixels, 25_000_000);
  assert.equal(getCapability("witness").limits.maxImagePixels, 16_000_000);
  assert.equal(getCapability("full").limits.maxVideoBytes, 500 * MIB);
  assert.equal(getCapability("full").limits.maxVideoDurationSeconds, 600);
  assert.equal(getCapability("full").limits.maxAnalyzedFrames, 1_200);
  assert.deepEqual(CAPABILITIES.map(({ timeoutMs }) => timeoutMs), [30_000, 30_000, 30_000, 60_000, 600_000]);
  assert.equal(Object.isFrozen(CAPABILITIES), true);
  assert.equal(Object.isFrozen(CAPABILITIES[0].fields[0]), true);
});

test("capability readiness is independent from the global core state", () => {
  const capabilities = {
    portrait: "unavailable",
    witness_blur: "ready",
    image_verification: "unavailable",
  };

  assert.equal(isCapabilityReady(capabilities, "witness_blur"), true);
  assert.equal(isCapabilityReady(capabilities, "portrait"), false);
  assert.equal(isCapabilityReady({}, "witness_blur"), false);
  assert.equal(isCapabilityReady(null, "witness_blur"), false);
});

test("getCapability supports canonical IDs, aliases and endpoints", () => {
  assert.equal(getCapability("images")?.id, "verify-images");
  assert.equal(getCapability("witness_blur")?.id, "witness");
  assert.equal(getCapability("image_verification")?.id, "verify-images");
  assert.equal(getCapability("video_quick")?.id, "verify-video-quick");
  assert.equal(getCapability("video_full")?.id, "verify-video-full");
  assert.equal(getCapability("/api/v2/verify/video/full")?.id, "verify-video-full");
  assert.equal(getCapability({ id: "video-quick" })?.id, "verify-video-quick");
  assert.equal(getCapability("missing"), null);
  assert.equal(getCapability(null), null);
});

test("validateSelection enforces required fields, formats, bytes and 25 MP", () => {
  const missing = validateSelection("verify-images", {
    reference_image: media("reference.jpg", 1, "image/jpeg", { width: 100, height: 100 }),
  });
  assert.equal(missing.valid, false);
  assert.ok(missing.errors.some(({ field, code }) => field === "probe_image" && code === "REQUIRED"));

  const oversized = validateSelection("portrait", {
    image: media("photo.jpg", 15 * MIB + 1, "image/jpeg", { width: 5_000, height: 5_000 }),
  });
  assert.equal(oversized.valid, false);
  assert.ok(oversized.errors.some(({ code }) => code === "IMAGE_TOO_LARGE"));

  const pixelBomb = validateSelection("portrait", {
    image: media("photo.png", 1, "image/png", { width: 5_001, height: 5_000 }),
  });
  assert.equal(pixelBomb.valid, false);
  assert.ok(pixelBomb.errors.some(({ message }) => message.includes("25,000,000")));

  const unsupported = validateSelection("portrait", {
    image: media("photo.gif", 1, "image/gif", { width: 1, height: 1 }),
  });
  assert.ok(unsupported.errors.some(({ code }) => code === "UNSUPPORTED_FORMAT"));
});

test("validateSelection applies the stricter 16 MP witness limit", () => {
  const boundary = validateSelection("witness", {
    image: media("witness.jpg", 15 * MIB, "image/jpeg", { width: 4_000, height: 4_000 }),
  });
  assert.equal(boundary.valid, true);

  const exceeded = validateSelection("witness", {
    image: media("witness.jpg", 1, "image/jpeg", { width: 4_001, height: 4_000 }),
  });
  assert.equal(exceeded.valid, false);
  assert.ok(exceeded.errors.some(({ code }) => code === "IMAGE_TOO_LARGE"));
});

test("validateSelection enforces 500 MiB, 600 seconds and 120 FPS for video", () => {
  const valid = validateSelection("verify-video-quick", {
    reference_image: media("reference.png", 1, "image/png", { width: 100, height: 100 }),
    video: media("capture.mp4", 500 * MIB, "video/mp4", { durationSeconds: 600, fps: 120 }),
  });
  assert.equal(valid.valid, true);

  const invalid = validateSelection("verify-video-full", {
    reference_image: media("reference.jpg", 1, "image/jpeg", { width: 100, height: 100 }),
    video: media("capture.mp4", 500 * MIB + 1, "video/mp4", { durationSeconds: 600.01, fps: 120.01 }),
  });
  assert.equal(invalid.valid, false);
  assert.ok(invalid.errors.some(({ code }) => code === "VIDEO_TOO_LARGE"));
  assert.ok(invalid.errors.some(({ code }) => code === "VIDEO_TOO_LONG"));
  assert.ok(invalid.errors.some(({ code }) => code === "VIDEO_FPS_TOO_HIGH"));
});

test("reason translation is stable, deduplicated and preserves unknown codes", () => {
  assert.equal(REASON_LABELS.THRESHOLD_UNCALIBRATED, "身份阈值尚未标定");
  assert.deepEqual(
    translateReasons(["NO_FACE", "NO_FACE", "SOMETHING_NEW"]),
    ["未检测到人脸", "SOMETHING NEW"],
  );
});

test("getSuggestionView fails closed for uncalibrated and wrong-dimension results", () => {
  const uncalibrated = getSuggestionView({
    success: true,
    suggestion: "PASS",
    reason_codes: ["THRESHOLD_UNCALIBRATED"],
    feature_dimension: EXPECTED_FEATURE_DIMENSION,
    calibration: { status: "UNCALIBRATED" },
  });
  assert.equal(uncalibrated.key, "review");
  assert.equal(uncalibrated.canTreatAsPass, false);
  assert.ok(uncalibrated.notices.some((notice) => notice.includes("不能作为通过依据")));

  const mismatch = getSuggestionView({
    success: true,
    suggestion: "PASS",
    reason_codes: [],
    feature_dimension: 512,
    calibration: { status: "ACTIVE" },
  });
  assert.equal(mismatch.key, "error");
  assert.equal(mismatch.canTreatAsPass, false);

  const pass = getSuggestionView({
    success: true,
    suggestion: "PASS",
    reason_codes: [],
    feature_dimension: EXPECTED_FEATURE_DIMENSION,
    calibration: { status: "ACTIVE" },
  });
  assert.equal(pass.key, "pass");
  assert.equal(pass.canTreatAsPass, true);
});

test("liveness=false is shown as an explicit non-liveness notice", () => {
  const view = getSuggestionView({
    success: true,
    suggestion: "REVIEW",
    reason_codes: ["IDENTITY_UNCERTAIN"],
    feature_dimension: 1024,
    liveness_evaluated: false,
  });
  assert.ok(view.notices.some((notice) => notice.includes("未评估活体")));
});

test("buildResultDetails allow-lists compact values and masks trace IDs", () => {
  const result = {
    success: true,
    suggestion: "REVIEW",
    reason_codes: ["THRESHOLD_UNCALIBRATED"],
    config_version: "2026-08-04-v1",
    processing_time_ms: 12.34,
    trace_id: "trace-very-sensitive-123456",
    similarity_score: 0.7654321,
    decision_threshold: null,
    feature_dimension: 1024,
    calibration: { status: "UNCALIBRATED" },
    reference: { analyzed: true, usable: true },
    probe: { analyzed: true, usable: false, reason_codes: ["TOO_BLURRY"] },
    image_base64: "A".repeat(20_000),
    embedding: [0.1, 0.2],
  };
  const details = buildResultDetails("verify-images", result);
  const text = JSON.stringify(details);
  assert.match(text, /1024 维/);
  assert.match(text, /未标定/);
  assert.match(text, /trac…3456/);
  assert.doesNotMatch(text, /trace-very-sensitive-123456/);
  assert.doesNotMatch(text, /image_base64|embedding|AAAA/);
  assert.match(text, /"value":"未标定"/);
  assert.doesNotMatch(text, /决策阈值","value":"0\.0000/);
});

test("witness details treat card recognition as optional protection", () => {
  const personOnly = buildResultDetails("witness", {
    success: true,
    suggestion: "PASS",
    reason_codes: [],
    card_regions: [],
    card_candidate_count: 2,
    protected_area_ratio: 0.42,
  });
  assert.equal(
    personOnly.find(({ key }) => key === "protection-mode")?.value,
    "人物保护已完成，证卡识别非必需",
  );

  const trustedCard = buildResultDetails("witness", {
    success: true,
    suggestion: "PASS",
    reason_codes: [],
    card_regions: [{ corners: [] }],
    card_candidate_count: 1,
    protected_area_ratio: 0.48,
  });
  assert.equal(
    trustedCard.find(({ key }) => key === "protection-mode")?.value,
    "可信证卡已识别，人物与证卡已保护",
  );
});

test("video details never misrepresent liveness=false", () => {
  const details = buildResultDetails("verify-video-full", {
    success: true,
    suggestion: "REVIEW",
    reason_codes: ["THRESHOLD_UNCALIBRATED"],
    mode: "full",
    duration_seconds: 20,
    frames: { valid_frames: 18, analyzed_frames: 20 },
    feature_dimension: 1024,
    calibration: { status: "UNCALIBRATED" },
    liveness_evaluated: false,
  });
  const liveness = details.find(({ key }) => key === "liveness");
  assert.equal(liveness.value, "未评估（不得视为活体通过）");
});

test("dataUrlToBlob decodes base64 and percent-encoded payloads without DOM APIs", async () => {
  const image = dataUrlToBlob("data:image/png;base64,iVBORw0KGgo=");
  assert.equal(image.type, "image/png");
  assert.deepEqual([...new Uint8Array(await image.arrayBuffer())], [137, 80, 78, 71, 13, 10, 26, 10]);

  const text = dataUrlToBlob("data:text/plain,hello%20world");
  assert.equal(await text.text(), "hello world");
  assert.throws(() => dataUrlToBlob("https://example.test/not-data"), /Expected a data URL/);
  assert.throws(() => dataUrlToBlob("data:image/png;base64,%%%"), /Invalid base64/);
});

test("safeJsonText summarizes base64 and recursively blocks private keys", () => {
  const payload = {
    feature_dimension: 1024,
    feature_version: "v2",
    image_base64: "A".repeat(4_096),
    nested: {
      embeddings: [0.1, 0.2],
      features: [0.3],
      face_embedding: ["private-face-embedding"],
      feature_vector: ["private-feature-vector"],
      debug_vector: ["private-debug-vector"],
      biometricVectors: ["private-biometric-vector"],
      face_feature_data: ["private-face-feature-data"],
      latent_features: ["private-latent-features"],
      alpha: [1, 0],
      matte: "secret-matte",
      raw_image: "secret-image",
      raw_frame: "secret-frame",
      access_token: "secret-token",
      cookie: "secret-cookie",
      authorization: "Bearer secret",
      video_path: "/private/video.mp4",
      accessToken: "secret-camel-token",
      filePath: "/private/camel-path",
    },
    ordinary_metadata: {
      feature_flag: "ordinary-feature-flag",
      supported_features: "ordinary-supported-features",
      feature_count: 5,
      motion_vector: "ordinary-motion-vector",
      version_vector: "ordinary-version-vector",
      vector_clock: "ordinary-vector-clock",
      debug_message: "ordinary-debug-message",
      face_count: 1,
    },
    imageBase64: "B".repeat(2_048),
    markup: "</script><img src=x>",
  };
  const text = safeJsonText(payload);
  assert.match(text, /image_base64/);
  assert.match(text, /4,096 字符/);
  assert.match(text, /feature_dimension/);
  assert.match(text, /1024/);
  assert.match(text, /feature_version/);
  assert.doesNotMatch(text, /private-face-embedding|private-feature-vector|private-debug-vector|private-biometric-vector|private-face-feature-data|private-latent-features/);
  assert.doesNotMatch(text, /secret-token|secret-cookie|secret-matte|private\/video|secret-camel|camel-path|AAAAAA|BBBBBB/);
  assert.match(text, /ordinary-feature-flag/);
  assert.match(text, /ordinary-supported-features/);
  assert.match(text, /ordinary-motion-vector/);
  assert.match(text, /ordinary-version-vector/);
  assert.match(text, /ordinary-vector-clock/);
  assert.match(text, /ordinary-debug-message/);
  assert.doesNotMatch(text, /<\/script>|<img/);
  assert.match(text, /已屏蔽敏感字段/);
});

test("safeUploadFilename never forwards a customer basename", () => {
  assert.equal(
    safeUploadFilename({ kind: "image" }, media("张三-身份证.jpg", 100, "image/jpeg").file),
    "visual-console-image.jpg",
  );
  assert.equal(
    safeUploadFilename({ kind: "video" }, media("客户签约记录.MOV", 100, "").file),
    "visual-console-video.mov",
  );
  assert.equal(
    safeUploadFilename({ kind: "video" }, media("private-name", 100, "video/webm").file),
    "visual-console-video.webm",
  );
  assert.equal(
    safeUploadFilename({ kind: "image" }, media("张三证件.PNG", 100, "image/jpeg").file),
    "visual-console-image.jpg",
  );
  assert.equal(
    safeUploadFilename({ kind: "image" }, media("C:\\Users\\张三\\现场照片.PNG", 100, "").file),
    "visual-console-image.png",
  );
  assert.equal(
    safeUploadFilename({ kind: "video" }, media("../../客户私密录像.exe", 100, "").file),
    "visual-console-video.mp4",
  );
});

test("safeJsonText handles cycles and bounds unkeyed arrays", () => {
  const cyclic = { values: Array.from({ length: 120 }, (_, index) => index) };
  cyclic.self = cyclic;
  const text = safeJsonText(cyclic, { maxArrayLength: 10 });
  assert.match(text, /循环引用/);
  assert.match(text, /另有110项已省略/);
});
