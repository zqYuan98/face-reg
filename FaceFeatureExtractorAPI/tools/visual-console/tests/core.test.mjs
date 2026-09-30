import assert from "node:assert/strict";
import test from "node:test";
import * as core from "../core.js";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";

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

test("portrait success with warnings remains a generated image, not identity approval", () => {
  const result = { success: true, suggestion: "REVIEW", reason_codes: ["TOO_DARK", "PORTRAIT_SOURCE_LIMITED"],
    image_base64: "/9j/", output_format: "jpeg" };
  const view = getSuggestionView(result, "portrait");
  assert.equal(view.label, "已生成，存在质量提醒");
  assert.equal(view.generated, true);
  assert.equal(view.canTreatAsPass, false);
  assert.doesNotMatch(view.message, /标定|可靠通过|重新采集/);
  assert.equal(buildResultDetails("portrait", result).find(item => item.key === "suggestion").value, view.label);
  assert.equal(getCapability("portrait").label, "白底人脸照");
  assert.deepEqual(getCapability("portrait").fields.map(field => field.name), ["image"]);
  assert.match(translateReasons(["PORTRAIT_SOURCE_LIMITED", "PORTRAIT_UPSCALE_LIMITED"]).join(" "), /源图.*放大/);
});

test("portrait clean success means image processing only; invalid warning results fail closed", () => {
  const clean = { success: true, suggestion: "PASS", reason_codes: [], image_base64: "/9j/" };
  assert.equal(getSuggestionView(clean, "portrait").label, "白底人脸照已生成");
  assert.equal(getSuggestionView(clean, "portrait").canTreatAsPass, false);
  for (const invalid of [
    { ...clean, image_base64: null }, { ...clean, suggestion: "REVIEW" },
    { ...clean, suggestion: "REVIEW", reason_codes: ["FACE_CROPPED"] },
    { ...clean, reason_codes: ["TOO_DARK"] },
  ]) assert.notEqual(getSuggestionView(invalid, "portrait").generated, true);
  assert.notEqual(getSuggestionView({ ...clean, success: false, suggestion: "RECAPTURE",
    reason_codes: ["MATTING_FAILED"], image_base64: null }, "portrait").generated, true);
  assert.equal(getSuggestionView({ ...clean, suggestion: "REVIEW", reason_codes: ["TOO_DARK"] }, "witness").label,
    "需要人工复核");
});

function media(name, size, type, metadata = {}) {
  return {
    file: { name, size, type },
    ...metadata,
  };
}

test("CAPABILITIES freezes the exact six v2 contracts", () => {
  assert.deepEqual(CAPABILITIES.map(({ id }) => id), [
    "portrait",
    "witness",
    "verify-images",
    "verify-video-quick",
    "verify-video-full",
    "verify-video-presence",
  ]);
  assert.deepEqual(
    CAPABILITIES.map(({ endpoint }) => endpoint),
    [
      "/api/v2/portrait/generate",
      "/api/v2/witness/blur-background",
      "/api/v2/verify/images",
      "/api/v2/verify/video/quick",
      "/api/v2/verify/video/full",
      "/api/v2/verify/video/presence",
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
  assert.deepEqual(CAPABILITIES.map(({ timeoutMs }) => timeoutMs), [30_000, 30_000, 30_000, 60_000, 600_000, 600_000]);
  assert.equal(Object.isFrozen(CAPABILITIES), true);
  assert.equal(Object.isFrozen(CAPABILITIES[0].fields[0]), true);
});

function presenceResult(overrides = {}) {
  return { mode: "presence", success: true, suggestion: "REVIEW", decision: "FOUND", found: true,
    comparison_status: "COMPARED", calibration_status: "CONFIGURED_UNVALIDATED", threshold: 0.7,
    threshold_id: "synthetic-only", policy_fingerprint: "a".repeat(64), feature_dimension: 1024,
    feature_version: "recognizer_v1_1024", matcher_version: "l2_dot_v1", liveness_evaluated: false,
    reason_codes: [], quality_warnings: ["TOO_DARK"], model_versions: {}, config_version: "test-v2",
    processing_time_ms: 12, trace_id: "synthetic", best_score: 0.9, best_timestamp_seconds: 0.5,
    evidence_frames: [{ frame_index: 15, timestamp_seconds: 0.5, score: 0.9, candidate_index: 1, quality_warnings: ["TOO_DARK"] }],
    scan_complete: true, sampling_strategy: "presence_adaptive_v1", requested_sampling_interval_seconds: 0.5,
    sampling_interval_seconds: 0.5, base_frames: 2, supplemental_frames: 1, sampled_frames: 3, decoded_frames: 31,
    valid_face_frames: 1, candidate_count: 2, compared_candidates: 2, failed_candidates: 0,
    candidate_limit_hit: false, duration_seconds: 1, timestamp_source: "pts", ...overrides };
}

test("presence displays the backend decision above REVIEW and never grants business pass", () => {
  for (const [result, decision, label] of [
    [presenceResult(), "FOUND", /本次扫描检测到匹配/],
    [presenceResult({ threshold: 0.95, decision: "NOT_FOUND_IN_SCAN", found: false }), "NOT_FOUND_IN_SCAN", /本次扫描未检测到匹配/],
    [presenceResult({ threshold: null, threshold_id: null, calibration_status: "UNCALIBRATED",
      reason_codes: ["THRESHOLD_NOT_CONFIGURED"], decision: "INCONCLUSIVE", found: null }), "INCONCLUSIVE", /比对已完成，尚未配置判定阈值/],
  ]) {
    const view = getSuggestionView(result);
    assert.equal(view.decision, decision);
    assert.equal(view.validContract, true);
    assert.match(view.label, label);
    assert.equal(view.canTreatAsPass, false);
    assert.doesNotMatch(view.notices.join(" "), /签名|活体.*失败|未标定.*任何/);
  }
});

test("presence keeps nullable identity and shows actual score, threshold, quality and sampling statistics", () => {
  const result = presenceResult({ threshold: null, threshold_id: null, calibration_status: "UNCALIBRATED",
    reason_codes: ["THRESHOLD_NOT_CONFIGURED"], decision: "INCONCLUSIVE", found: null });
  const view = getSuggestionView(result);
  assert.equal(view.key, "inconclusive");
  assert.equal(view.canTreatAsPass, false);
  assert.equal(getSuggestionView({ ...result, decision: "FOUND", found: true }).key, "inconclusive");
  assert.equal(getSuggestionView({ ...result, decision: "FOUND", found: true, calibration_status: undefined }).key, "inconclusive");
  assert.equal(getSuggestionView({ ...result, best_score: null, best_timestamp_seconds: null, reason_codes: ["NO_FACE"] }).key, "inconclusive");
  const details = buildResultDetails("presence", result);
  const values = Object.fromEntries(details.map(({ key, value }) => [key, value]));
  assert.equal(values.found, "未确定（null）");
  assert.equal(values["best-score"], "0.9000");
  assert.equal(values["best-timestamp"], "0.500 秒");
  assert.equal(values["scan-complete"], "是（仅指采样计划）");
  assert.equal(values["candidate-count"], "2");
  assert.equal(values["sampling-interval"], "0.500 秒");
  assert.equal(values["requested-sampling-interval"], "0.500 秒");
  assert.equal(values["base-frames"], "2");
  assert.equal(values["supplemental-frames"], "1");
  assert.equal(values["compared-candidates"], "2");
  assert.equal(values["failed-candidates"], "0");
  assert.equal(values.threshold, "未配置");
  assert.match(values["quality-warnings"], /画面过暗/);
  assert.equal(values["timestamp-source"], "pts");
  const configured = Object.fromEntries(buildResultDetails("presence", presenceResult()).map(({ key, value }) => [key, value]));
  assert.equal(configured.decision, "FOUND");
  assert.equal(configured.found, "是（true）");
  assert.equal(configured.threshold, "0.7000");
  assert.match(configured.calibration, /试用.*未验证/);
  assert.equal(getCapability("video_presence")?.timeoutMs, getCapability("full").timeoutMs);
  assert.deepEqual(getCapability("presence").fields.map(({ name }) => name), ["reference_image", "video"]);
});

test("presence rejects contradictory configured and unknown responses without deriving a decision", () => {
  const invalid = [
    { decision: "SOMETHING_NEW" }, { found: false }, { success: false }, { scan_complete: false },
    { best_score: 0.6 }, { threshold: 0.9 + Number.EPSILON }, { threshold: 1 }, { threshold: -1 },
    { threshold: "0.7" }, { threshold_id: " " }, { policy_fingerprint: "bad" }, { suggestion: "PASS" },
    { comparison_status: "UNKNOWN" }, { comparison_status: "NO_COMPARABLE_FACE" },
    { comparison_status: undefined }, { sampling_strategy: "unknown" },
    { requested_sampling_interval_seconds: 0 }, { base_frames: 1 }, { supplemental_frames: -1 },
    { sampled_frames: 32 }, { valid_face_frames: 4 }, { candidate_count: 99 }, { compared_candidates: 0 },
    { failed_candidates: 1 }, { candidate_count: 1 }, { decoded_frames: 15 },
    { quality_warnings: "TOO_DARK" }, { quality_warnings: [123] },
    { quality_warnings: [] }, { evidence_frames: [] }, { best_timestamp_seconds: 0.6 },
    { evidence_frames: [...presenceResult().evidence_frames, ...presenceResult().evidence_frames] },
    { evidence_frames: [{ ...presenceResult().evidence_frames[0], candidate_index: 5 }] },
    { evidence_frames: [{ ...presenceResult().evidence_frames[0], quality_warnings: [false] }] },
    { decision: "NOT_FOUND_IN_SCAN", found: false },
    { calibration_status: "ACTIVE" }, { liveness_evaluated: true },
    { decision: "INCONCLUSIVE", found: null },
    { threshold: 0.95, decision: "INCONCLUSIVE", found: null },
    { requested_sampling_interval_seconds: 1 }, { policy_fingerprint: "A".repeat(64) },
    { threshold_id: "x".repeat(129) }, { reason_codes: ["THRESHOLD_NOT_CONFIGURED"] },
    { candidate_count: 3 }, { suggestion: "RECAPTURE" },
  ];
  for (const overrides of invalid) {
    const result = presenceResult(overrides);
    const view = getSuggestionView(result);
    assert.equal(view.validContract, false, JSON.stringify(overrides));
    assert.match(view.label, /不可用/, JSON.stringify(overrides));
    assert.equal(view.canTreatAsPass, false);
    assert.notEqual(view.decision, "FOUND");
  }
  const ambiguous = presenceResult({ decision: "INCONCLUSIVE", found: null, scan_complete: false });
  assert.equal(getSuggestionView(ambiguous).decision, "INCONCLUSIVE");
  assert.equal(getSuggestionView(ambiguous).validContract, true);
  // Even a valid high score does not authorize changing an explicit backend INCONCLUSIVE.
  assert.notEqual(getSuggestionView(ambiguous).decision, "FOUND");
});

test("presence allows hits despite local failures but excludes unsupported negative conclusions", () => {
  for (const overrides of [{ candidate_limit_hit: true, reason_codes: ["CANDIDATE_LIMIT_REACHED"] },
    { failed_candidates: 1, candidate_count: 3 }]) {
    assert.equal(getSuggestionView(presenceResult(overrides)).decision, "FOUND");
    const negative = presenceResult({ ...overrides, threshold: 0.95, decision: "NOT_FOUND_IN_SCAN", found: false });
    assert.equal(getSuggestionView(negative).validContract, false);
    assert.equal(getSuggestionView({ ...negative, decision: "INCONCLUSIVE", found: null }).validContract, true);
  }
});

test("presence distinguishes no comparable face, invalid reference and global failure", () => {
  const empty = { best_score: null, best_timestamp_seconds: null, evidence_frames: [], compared_candidates: 0,
    valid_face_frames: 0, candidate_count: 0, quality_warnings: [], decision: "INCONCLUSIVE", found: null };
  for (const [status, overrides, label] of [
    ["NO_COMPARABLE_FACE", {}, /未获得可比人脸/],
    ["INVALID_REFERENCE", { scan_complete: false, sampled_frames: 0, base_frames: 0, supplemental_frames: 0, decoded_frames: 0 }, /参考.*不可用/],
    ["FAILED", { success: false, scan_complete: false }, /处理失败/],
  ]) {
    const view = getSuggestionView(presenceResult({ ...empty, ...overrides, comparison_status: status }));
    assert.equal(view.validContract, true, status);
    assert.match(view.label, label);
    assert.equal(view.decision, "INCONCLUSIVE");
  }
  const failedWithEvidence = getSuggestionView(presenceResult({ success: false, scan_complete: false,
    comparison_status: "FAILED", decision: "INCONCLUSIVE", found: null }));
  assert.equal(failedWithEvidence.validContract, true);
});

test("presence aligns unconfigured failure, fingerprint and warning constraints with the DTO", () => {
  const result = presenceResult({ success: false, scan_complete: false, comparison_status: "FAILED",
    threshold: null, threshold_id: null, calibration_status: "UNCALIBRATED",
    reason_codes: ["THRESHOLD_NOT_CONFIGURED"], decision: "INCONCLUSIVE", found: null });
  assert.equal(getSuggestionView(result).validContract, true);
  for (const overrides of [{ suggestion: "RECAPTURE" }, { policy_fingerprint: null },
    { quality_warnings: ["TOO_DARK", "TOO_DARK"] }, { quality_warnings: ["INFERENCE_ERROR"] }]) {
    assert.equal(getSuggestionView({ ...result, ...overrides }).validContract, false, JSON.stringify(overrides));
  }
});

test("unfinished unconfigured comparisons never claim the scan is complete", () => {
  const result = presenceResult({ scan_complete: false, threshold: null, threshold_id: null,
    calibration_status: "UNCALIBRATED", reason_codes: ["THRESHOLD_NOT_CONFIGURED"], decision: "INCONCLUSIVE", found: null });
  const view = getSuggestionView(result);
  assert.equal(view.validContract, true);
  assert.match(view.label, /扫描未完成/);
  assert.doesNotMatch(view.label, /比对已完成/);
  const details = Object.fromEntries(buildResultDetails("presence", result).map(({ key, value }) => [key, value]));
  assert.equal(details["best-score"], "0.9000");
  assert.equal(details["scan-complete"], "否（扫描未完整覆盖）");
});

test("presence contract verifies the original mode for modern and legacy responses", () => {
  const legacy = { ...presenceResult(), calibration_status: "UNCALIBRATED", decision: "INCONCLUSIVE", found: null,
    reason_codes: ["CALIBRATION_REQUIRED", "THRESHOLD_UNCALIBRATED"] };
  for (const field of ["comparison_status", "threshold", "threshold_id", "policy_fingerprint", "compared_candidates",
    "quality_warnings", "sampling_strategy", "base_frames", "supplemental_frames", "failed_candidates",
    "requested_sampling_interval_seconds"]) delete legacy[field];
  assert.equal(getSuggestionView(legacy).validContract, true);
  for (const original of [presenceResult(), legacy]) {
    for (const mode of ["quick", "full", "images", undefined, null]) {
      const result = { ...original, mode };
      const view = getSuggestionView(result, "presence");
      assert.equal(view.validContract, false, String(mode));
      assert.match(view.label, /不可用/);
      const details = Object.fromEntries(buildResultDetails("presence", result).map(({ key, value }) => [key, value]));
      assert.equal(details.decision, "不可用");
      assert.equal(details.found, "不可用");
    }
  }
});

async function consoleApp(fetch) {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const nodes = new Map([
    ["#result-content", { hidden: true }], ["#result-empty", { hidden: false }],
    ["#result-suggestion", { textContent: "" }], ["#result-status", { textContent: "", dataset: {} }],
    ["#result-metrics", evidenceBody()], ["#result-reasons", evidenceBody()],
    ["#evidence-frames-body", evidenceBody()],
  ]);
  const document = { body: { dataset: {} }, querySelector: (selector) => nodes.get(selector) ?? null,
    querySelectorAll: () => [], createElement(tag) {
      if (tag !== "video") return { tag, children: [], dataset: {}, append(...items) { this.children.push(...items); } };
      return { duration: 1, videoWidth: 100, videoHeight: 100, removeAttribute() {}, load() {},
        set src(value) { queueMicrotask(() => this.onloadedmetadata()); } };
    } };
  const context = { ...core, document, fetch, URL, FormData, AbortSignal, performance, CSS: { escape: (value) => value },
    window: { clearTimeout() {}, setTimeout() {} } };
  // Run the real app functions in a small DOM environment; omit only page startup/polling.
  runInNewContext(source.slice(0, source.lastIndexOf("\nbindEvents();"))
    .replace(/^import\s*\{[\s\S]*?\}\s*from\s*"\.\/core\.js";/, "")
    + "\nglobalThis.app = { state, nodes, runCurrentTest, handleSelectedFile, clearField, clearResult, renderResult };", context);
  const app = context.app;
  app.state.capabilityId = "verify-video-presence";
  app.state.capabilities = { video_presence: "ready" };
  app.state.csrfToken = "t".repeat(32);
  app.state.csrfExpiresAt = Date.now() + 900_000;
  app.state.files.set("reference_image", new File(["image"], "reference.png", { type: "image/png" }));
  app.state.files.set("video", new File(["video-a"], "a.mp4", { type: "video/mp4" }));
  app.state.metadata.set("reference_image", { width: 100, height: 100 });
  app.state.metadata.set("video", { durationSeconds: 1, fps: 30 });
  return app;
}

test("delayed presence responses cannot attach old evidence to replacement media", async () => {
  for (const rejectResponse of [false, true]) {
    let release;
    let reached;
    let uploadedVideo;
    const started = new Promise((resolve) => { reached = resolve; });
    const response = new Promise((resolve, reject) => { release = rejectResponse ? reject : resolve; });
    const app = await consoleApp((url, request) => {
      uploadedVideo = request.body.get("video");
      reached();
      return response;
    });
    const pending = app.runCurrentTest({ preventDefault() {} });
    await started;
    assert.equal(app.state.running, true);
    await app.handleSelectedFile(getCapability("presence").fields[1], new File(["video-b"], "b.mp4", { type: "video/mp4" }));
    assert.equal(await uploadedVideo.text(), "video-a");
    release(rejectResponse ? new Error("old request failed") : { status: 200, ok: true, text: async () => JSON.stringify(presenceResult()) });
    await pending;
    assert.equal(app.state.running, false);
    assert.equal(app.nodes.resultContent.hidden, true, "stale response must not render against replacement media");
    assert.equal(app.nodes.evidenceBody.children.length, 0);
    assert.equal(await app.state.files.get("video").text(), "video-b");
  }
});

test("presence endpoint renders only original presence mode and accepts a current request", async () => {
  for (const mode of ["presence", "quick", "full", undefined]) {
    const app = await consoleApp(async () => ({ status: 200, ok: true,
      text: async () => JSON.stringify(presenceResult({ mode })) }));
    await app.runCurrentTest({ preventDefault() {} });
    assert.equal(app.state.running, false);
    assert.equal(app.nodes.resultContent.hidden, false);
    if (mode === "presence") {
      assert.equal(app.nodes.resultStatus.textContent, "FOUND");
      assert.equal(app.nodes.evidenceBody.children.length, 1);
    } else {
      assert.equal(app.nodes.resultStatus.textContent, "UNAVAILABLE");
      assert.match(app.nodes.resultSuggestion.textContent, /不可用/);
      assert.equal(app.nodes.evidenceBody.children.length, 0);
    }
  }
});

test("presence evidence is bounded numeric text rendered without HTML interpolation", () => {
  assert.equal(typeof core.renderEvidenceFrames, "function");
  const makeNode = (tag) => ({ tag, children: [], textContent: "", append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; } });
  const body = makeNode("tbody");
  body.ownerDocument = { createElement: makeNode };
  const evidence = [{ frame_index: 12, timestamp_seconds: 0.5, score: 0.999 },
    { frame_index: "<img src=x onerror=alert(1)>", timestamp_seconds: 1, score: 1 },
    ...Array.from({ length: 9 }, (_, i) => ({ frame_index: i + 20, timestamp_seconds: i + 1, score: 0.7 }))];
  core.renderEvidenceFrames(body, { evidence_frames: evidence });
  assert.equal(body.children.length, 5);
  assert.deepEqual(body.children[0].children.map((cell) => cell.textContent), ["12", "0.500", "0.9990", "—", "旧响应未提供"]);
  assert.ok(body.children.every((row) => row.children.every((cell) => !String(cell.textContent).includes("<"))));
  core.renderEvidenceFrames(body, { evidence_frames: [] });
  assert.equal(body.children.length, 0);
});

function evidenceBody() {
  const makeNode = (tag) => ({ tag, children: [], textContent: "", handlers: {},
    append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
    addEventListener(name, listener) { this.handlers[name] = listener; },
    setAttribute(name, value) { this[name] = value; } });
  const body = makeNode("tbody");
  body.ownerDocument = { createElement: makeNode };
  return body;
}

test("presence evidence uses accessible seek buttons and clears stale interactions on reset", () => {
  const body = evidenceBody();
  const sought = [];
  core.renderEvidenceFrames(body, presenceResult(), { onSeek: (seconds) => sought.push(seconds) });
  const cells = body.children[0].children;
  const button = cells[1].children[0];
  assert.equal(button?.tag, "button");
  assert.equal(button.type, "button");
  assert.equal(button.textContent, "0.500 秒");
  assert.match(button["aria-label"], /本地.*0\.500/);
  assert.equal(cells[3].textContent, "1");
  assert.match(cells[4].textContent, /画面过暗/);
  button.handlers.click();
  assert.deepEqual(sought, [0.5]);
  core.renderEvidenceFrames(body, {});
  assert.equal(body.children.length, 0);
  button.handlers.click();
  assert.deepEqual(sought, [0.5]);
  core.renderEvidenceFrames(body, presenceResult());
  assert.equal(body.children[0].children[1].children.length, 0);
});

test("evidence seek only changes the currently uploaded local preview and never fetches media", () => {
  assert.equal(typeof core.seekEvidencePreview, "function");
  const preview = { src: "blob:synthetic-local", duration: 2, currentTime: 0,
    pause() { this.paused = true; }, focus() { this.focused = true; }, scrollIntoView() { this.scrolled = true; } };
  assert.equal(core.seekEvidencePreview(preview, "blob:synthetic-local", 1.25), true);
  assert.equal(preview.currentTime, 1.25);
  assert.equal(preview.src, "blob:synthetic-local");
  assert.equal(preview.paused, true);
  assert.equal(preview.focused, true);
  for (const [video, url, time] of [[preview, "https://example.test/video.mp4", 1],
    [preview, "blob:stale", 1], [preview, "blob:synthetic-local", 9], [preview, "blob:synthetic-local", -1],
    [preview, "blob:synthetic-local", NaN], [null, "blob:synthetic-local", 1]]) {
    assert.equal(core.seekEvidencePreview(video, url, time), false);
  }
  assert.equal(preview.currentTime, 1.25);
});

test("presence v1 rejects forged active decisions and never labels them calibrated or found", () => {
  const valid = { mode: "presence", success: true, suggestion: "REVIEW", decision: "INCONCLUSIVE", found: null,
    calibration_status: "UNCALIBRATED", feature_dimension: 1024, feature_version: "recognizer_v1_1024", matcher_version: "l2_dot_v1",
    liveness_evaluated: false, reason_codes: ["CALIBRATION_REQUIRED", "THRESHOLD_UNCALIBRATED"], model_versions: {},
    config_version: "test-v1", processing_time_ms: 1, trace_id: "test", best_score: null, best_timestamp_seconds: null,
    evidence_frames: [], scan_complete: true, sampling_interval_seconds: 0.5, sampled_frames: 2, decoded_frames: 30,
    valid_face_frames: 0, candidate_count: 0, candidate_limit_hit: false, duration_seconds: 1, timestamp_source: "pts" };
  assert.equal(getSuggestionView(valid).label, "无法确定目标是否出现");
  const forged = { mode: "presence", success: true, suggestion: "PASS", feature_dimension: 1024,
    calibration_status: "ACTIVE", decision: "FOUND", found: true, liveness_evaluated: true };
  for (const result of [forged, { ...forged, decision: "NOT_FOUND_IN_SCAN", found: false, scan_complete: true },
    { ...forged, calibration_status: "UNCALIBRATED", decision: "INCONCLUSIVE", found: null },
    { mode: "presence" }, { ...valid, liveness_evaluated: true }, { ...valid, evidence_frames: undefined },
    { ...valid, calibration_status: "ACTIVE", decision: "FOUND", found: true }]) {
    const view = getSuggestionView(result);
    assert.equal(view.key, "inconclusive");
    assert.equal(view.canTreatAsPass, false);
    assert.match(view.label, /不可用|人工复核/);
    assert.doesNotMatch(view.label, /扫描中发现|已扫描范围未发现|已标定/);
    const values = Object.fromEntries(buildResultDetails("presence", result).map(({ key, value }) => [key, value]));
    assert.doesNotMatch(values.calibration, /已标定/);
    assert.doesNotMatch(values.decision, /FOUND/);
    assert.doesNotMatch(values.found, /true|false/);
  }
});

test("presence UI wires readiness, evidence and a bounded browser deadline", async () => {
  const app = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  assert.match(app, /"verify-video-presence": "video_presence"/);
  assert.match(app, /renderEvidenceFrames\(nodes\.evidenceBody/);
  assert.match(app, /signal: AbortSignal\.timeout\(capability\.timeoutMs \+ 30_000\)/);
  assert.match(app, /CAPABILITIES\.map\(\(capability\) => state\.capabilities\[READINESS_KEYS\[capability\.id\]\]\)/);
  assert.match(app.match(/function clearResult\(\) \{[\s\S]*?\n\}/)?.[0] ?? "", /renderEvidenceFrames\(nodes\.evidenceBody, \{\}\)/);
  assert.match(html, /data-capability="video_presence"/);
  assert.match(html, /id="evidence-frames-body"/);
  assert.doesNotMatch(app, /innerHTML|insertAdjacentHTML/);
  assert.doesNotMatch(app, /found: "FOUND"|"not-found": "NOT_FOUND_IN_SCAN"/);
  assert.match(app, /presence \? view\.decision/);
  assert.doesNotMatch(app, /presence \? "INCONCLUSIVE"/);
  assert.match(app, /onSeek: seekLocalEvidence/);
  assert.match(app, /seekEvidencePreview\(preview, state\.previewUrls\.get\("video"\), timestamp\)/);
  assert.match(app, /view\.validContract \? payload : \{\}/);
  assert.match(app, /if \(!presence\) renderDerivedImage\(payload\)/);
  assert.doesNotMatch(app, /首版未标定|所有身份结论均为 INCONCLUSIVE/);
  assert.match(html, /候选序号/);
  assert.match(html, /质量提示/);
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

function imageTrialResult(overrides = {}) {
  return {
    success: true, suggestion: "REVIEW", reason_codes: ["IMAGE_TRIAL_THRESHOLD_MATCHED"],
    similarity_score: 0.75, decision_threshold: 0.55, feature_dimension: 1024,
    calibration: { status: "CONFIGURED_UNVALIDATED", calibration_id: null, artifact_sha256: null },
    reference: { role: "reference", analyzed: true, usable: true, reason_codes: [] },
    probe: { role: "probe", analyzed: true, usable: true, reason_codes: [] },
    ...overrides,
  };
}

test("image trial threshold uses backend reasons at equality and both score boundaries", () => {
  for (const [value, threshold, matched] of [
    [0.5499999, 0.55, false], [0.55, 0.55, true], [0.5500001, 0.55, true],
    [-1, 0.55, false], [0, 0.55, false], [1, 0.55, true], [0, 0, true], [1, 1, true],
  ]) {
    const result = imageTrialResult({ similarity_score: value, decision_threshold: threshold,
      reason_codes: [matched ? "IMAGE_TRIAL_THRESHOLD_MATCHED" : "IMAGE_TRIAL_THRESHOLD_NOT_MATCHED"] });
    const view = getSuggestionView(result, "verify-images");
    assert.equal(view.label, matched ? "达到试用匹配阈值" : "未达到试用匹配阈值");
    assert.equal(view.key, "review");
    assert.equal(view.validContract, true);
    assert.equal(view.canTreatAsPass, false);
    assert.match([view.message, ...view.notices].join(" "), /试用阈值.*精度未验证.*业务仍需人工复核/);
    const details = Object.fromEntries(buildResultDetails("verify-images", result).map(({ key, value }) => [key, value]));
    assert.equal(details["similarity-score"], value.toFixed(4));
    assert.equal(details["decision-threshold"], threshold.toFixed(4));
    assert.equal(details.calibration, "试用阈值（精度未验证）");
    assert.doesNotMatch(JSON.stringify(details), /已标定|算法建议通过/);
  }
});

test("image trial rejects forged, contradictory and incomplete threshold responses", () => {
  const base = imageTrialResult();
  for (const overrides of [
    { suggestion: "PASS" }, { suggestion: "RECAPTURE" }, { success: false }, { success: "true" },
    { reason_codes: [] }, { reason_codes: ["IMAGE_TRIAL_THRESHOLD_NOT_MATCHED"] },
    { reason_codes: ["IMAGE_TRIAL_THRESHOLD_MATCHED", "IMAGE_TRIAL_THRESHOLD_NOT_MATCHED"] },
    { reason_codes: ["IMAGE_TRIAL_THRESHOLD_MATCHED", "IMAGE_TRIAL_THRESHOLD_MATCHED"] },
    { reason_codes: ["IMAGE_TRIAL_THRESHOLD_MATCHED", "NO_FACE"] },
    { reason_codes: "IMAGE_TRIAL_THRESHOLD_MATCHED" },
    { reason_codes: ["image_trial_threshold_matched"] },
    ...[null, undefined, NaN, Infinity, -Infinity, true, "0.75", -1.0001, 1.0001]
      .map(value => ({ similarity_score: value })),
    ...[null, undefined, NaN, Infinity, -Infinity, true, "0.55", -0.0001, 1.0001]
      .map(value => ({ decision_threshold: value })),
    { feature_dimension: 512 }, { feature_dimension: "1024" }, { feature_dimension: undefined },
    { reference: { ...base.reference, usable: false } },
    { reference: { ...base.reference, analyzed: false } },
    { probe: { ...base.probe, usable: "true" } },
    { probe: { ...base.probe, reason_codes: ["NO_FACE"] } },
    { probe: { ...base.probe, reason_codes: ["IMAGE_TRIAL_THRESHOLD_MATCHED"] } },
    { probe: { ...base.probe, quality: { reason_codes: ["IMAGE_TRIAL_THRESHOLD_MATCHED"] } } },
    { probe: { ...base.probe, role: "reference" } },
    { calibration: { ...base.calibration, calibration_id: "fake" } },
    { calibration: { ...base.calibration, artifact_sha256: "a".repeat(64) } },
    { calibration: { status: "CONFIGURED_UNVALIDATED" } },
    ...["ACTIVE", "UNCALIBRATED", "CONFIGURED", "UNKNOWN", "configured_unvalidated", undefined, null]
      .map(status => ({ calibration: { ...base.calibration, status } })),
    { calibration: null },
  ]) {
    const result = imageTrialResult(overrides);
    const view = getSuggestionView(result, "verify-images");
    assert.equal(view.label, "结果不可用", JSON.stringify(overrides));
    assert.equal(view.validContract, false, JSON.stringify(overrides));
    assert.equal(view.canTreatAsPass, false);
    const details = Object.fromEntries(buildResultDetails("verify-images", result).map(({ key, value }) => [key, value]));
    assert.equal(details.calibration, "不可用");
    assert.equal(details["decision-threshold"], "不可用");
    assert.equal(details["similarity-score"], "不可用");
  }
  const below = imageTrialResult({ similarity_score: 0.5499999 });
  assert.equal(getSuggestionView(below, "verify-images").validContract, false);
});

test("image trial quality and system failures retain their original advice without a match decision", () => {
  for (const [success, suggestion, reason, expected] of [
    [true, "RECAPTURE", "NO_FACE", "建议重新采集"],
    [true, "RECAPTURE", "FACE_TOO_SMALL", "建议重新采集"],
    [true, "RECAPTURE", "FACE_CROPPED", "建议重新采集"],
    [false, "REVIEW", "FEATURE_FAILED", "需要人工复核"],
    [false, "REVIEW", "INFERENCE_ERROR", "需要人工复核"],
  ]) {
    const result = imageTrialResult({ success, suggestion, similarity_score: null, reason_codes: [reason],
      probe: { role: "probe", analyzed: true, usable: false, reason_codes: [reason] } });
    const view = getSuggestionView(result, "verify-images");
    assert.equal(view.label, expected);
    assert.equal(view.validContract, true);
    assert.equal(view.canTreatAsPass, false);
    assert.ok(view.notices.some(notice => notice.includes(REASON_LABELS[reason])));
    assert.doesNotMatch(JSON.stringify(view), /未达到试用匹配阈值/);
    const details = Object.fromEntries(buildResultDetails("verify-images", result).map(({ key, value }) => [key, value]));
    assert.equal(details["similarity-score"], undefined);
    assert.equal(details["decision-threshold"], "0.5500");
    for (const trialCode of ["IMAGE_TRIAL_THRESHOLD_MATCHED", "IMAGE_TRIAL_THRESHOLD_NOT_MATCHED"]) {
      assert.equal(getSuggestionView({ ...result, reason_codes: [reason, trialCode] }, "verify-images").validContract, false);
      assert.equal(getSuggestionView({ ...result, probe: { ...result.probe, reason_codes: [trialCode] } }, "verify-images").validContract, false);
    }
  }
});

test("image trial does not alter signed, uncalibrated, generated-image or video interpretation", () => {
  const signed = imageTrialResult({ suggestion: "PASS", reason_codes: [],
    calibration: { status: "ACTIVE", calibration_id: "signed", artifact_sha256: "a".repeat(64) } });
  assert.equal(getSuggestionView(signed, "verify-images").canTreatAsPass, true);
  assert.equal(getSuggestionView({ ...signed, similarity_score: 0.2, suggestion: "REVIEW",
    reason_codes: ["IDENTITY_UNCERTAIN"] }, "verify-images").label, "需要人工复核");
  const uncalibrated = imageTrialResult({ decision_threshold: null,
    reason_codes: ["THRESHOLD_UNCALIBRATED"], calibration: { status: "UNCALIBRATED" } });
  assert.equal(getSuggestionView(uncalibrated, "verify-images").label, "需要人工复核");
  assert.equal(buildResultDetails("verify-images", uncalibrated).find(item => item.key === "decision-threshold").value, "未标定");
  for (const capability of ["verify-video-quick", "verify-video-full", "witness"]) {
    assert.equal(getSuggestionView(signed, capability).label, "算法建议通过");
    assert.equal(getSuggestionView(imageTrialResult(), capability).label, "需要人工复核");
    assert.doesNotMatch(JSON.stringify(buildResultDetails(capability, imageTrialResult())), /试用阈值（精度未验证）/);
  }
  assert.equal(getSuggestionView(imageTrialResult(), "images").label, "达到试用匹配阈值");
  for (const status of ["UNKNOWN", "CONFIGURED", "configured_unvalidated", undefined, null]) {
    const result = { ...signed, calibration: { status } };
    assert.equal(getSuggestionView(result, "verify-images").label, "结果不可用");
    assert.equal(getSuggestionView(result, "verify-images").canTreatAsPass, false);
  }
  assert.equal(getSuggestionView({ ...signed, probe: { ...signed.probe,
    reason_codes: ["IMAGE_TRIAL_THRESHOLD_MATCHED"] } }, "verify-images").validContract, false);
  assert.equal(getSuggestionView({ ...signed, calibration: undefined }, "verify-images").validContract, false);
});

test("image trial app preserves REVIEW badge and rejects success-shaped HTTP errors consistently", async () => {
  const app = await consoleApp(async () => { throw new Error("no request expected"); });
  app.state.capabilityId = "verify-images";
  app.renderResult(imageTrialResult(), 200, 5);
  assert.equal(app.nodes.resultStatus.textContent, "REVIEW");
  assert.equal(app.nodes.resultSuggestion.textContent, "达到试用匹配阈值");
  const metrics = () => Object.fromEntries(app.nodes.resultMetrics.children.map(item => [item.dataset.metric, item.children[1].textContent]));
  assert.equal(metrics()["decision-threshold"], "0.5500");
  app.renderResult(imageTrialResult(), 500, 5);
  assert.equal(app.nodes.resultStatus.textContent, "REVIEW");
  assert.equal(app.nodes.resultSuggestion.textContent, "结果不可用");
  assert.equal(metrics().suggestion, "结果不可用");
  assert.equal(metrics()["decision-threshold"], "不可用");
  assert.equal(app.nodes.resultReasons.children.some(item => /达到试用匹配阈值/.test(item.textContent)), false);
  assert.doesNotMatch(metrics().reasons, /达到试用匹配阈值/);
  app.clearResult();
  assert.equal(app.nodes.resultContent.hidden, true);
  assert.equal(app.nodes.resultMetrics.children.length, 0);
  assert.equal(app.nodes.resultReasons.children.length, 0);
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
