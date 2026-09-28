import {
  CAPABILITIES,
  buildResultDetails,
  dataUrlToBlob,
  getCapability,
  getSuggestionView,
  isCapabilityReady,
  renderEvidenceFrames,
  safeUploadFilename,
  safeJsonText,
  translateReasons,
  validateSelection,
} from "./core.js";

const CONTENT = Object.freeze({
  portrait: {
    eyebrow: "PORTRAIT GENERATION",
    title: "人脸白底头肩照生成",
    description: "上传一张清晰正面照，检查人像质量并生成标准白色背景头肩照。",
    action: "生成白底照",
  },
  witness: {
    eyebrow: "WITNESS BLUR",
    title: "见证照背景虚化",
    description: "优先保持人物清晰，识别到可信证卡时额外保护，并虚化人物之外的背景。",
    action: "开始背景虚化",
  },
  "verify-images": {
    eyebrow: "1 : 1 IMAGE VERIFY",
    title: "图片身份比对",
    description: "上传参考照片和待核验照片，查看相似度、质检结果与算法建议。",
    action: "开始图片比对",
  },
  "verify-video-quick": {
    eyebrow: "QUICK VIDEO VERIFY",
    title: "视频快速身份核验",
    description: "从少量代表帧检查持续身份一致性；不是检查目标是否曾出现。",
    action: "开始视频快检",
  },
  "verify-video-full": {
    eyebrow: "FULL VIDEO VERIFY",
    title: "视频完整身份核验",
    description: "分析整段采样帧和人物轨迹的持续身份一致性；不是检查目标是否曾出现，最长十分钟。",
    action: "开始视频全检",
  },
  "verify-video-presence": {
    eyebrow: "VIDEO TARGET PRESENCE",
    title: "视频目标出现",
    description: "扫描视频中的目标候选，显示最佳时刻与证据。首版未标定，所有身份结论均为 INCONCLUSIVE；不评估活体或反翻拍。",
    action: "扫描目标出现",
  },
});

const READINESS_KEYS = Object.freeze({
  portrait: "portrait",
  witness: "witness_blur",
  "verify-images": "image_verification",
  "verify-video-quick": "video_verification",
  "verify-video-full": "video_verification",
  "verify-video-presence": "video_presence",
});

const $ = (selector) => document.querySelector(selector);
const nodes = {
  form: $("#algorithm-form"),
  inputFields: $("#input-fields"),
  runButton: $("#run-button"),
  resetButton: $("#reset-button"),
  serviceStatus: $("#service-status-label"),
  serviceStatusContainer: $("#service-status"),
  readyModelCount: $("#ready-model-count"),
  readyCapabilityCount: $("#ready-capability-count"),
  readinessCheckedAt: $("#readiness-checked-at"),
  refreshReadiness: $("#refresh-readiness"),
  resultEmpty: $("#result-empty"),
  resultContent: $("#result-content"),
  resultSummary: $("#result-summary"),
  resultStatus: $("#result-status"),
  resultSuggestion: $("#result-suggestion"),
  resultMessage: $("#result-message"),
  resultReasons: $("#result-reasons"),
  resultDuration: $("#result-duration"),
  resultTraceId: $("#result-trace-id"),
  resultMetrics: $("#result-metrics"),
  evidencePanel: $("#evidence-frames-panel"),
  evidenceBody: $("#evidence-frames-body"),
  derivedPanel: $("#derived-image-panel"),
  derivedImage: $("#derived-image"),
  downloadResult: $("#download-result"),
  rawJson: $("#raw-json"),
  notification: $("#notification-region"),
  workspaceEyebrow: $("#workspace-eyebrow"),
  workspaceTitle: $("#active-capability-title"),
  workspaceDescription: $("#active-capability-description"),
  endpointLabel: $("#active-endpoint"),
  timeoutLabel: $("#input-deadline"),
  inputRequirements: $("#input-requirements"),
  formError: $("#form-error"),
  requestProgress: $("#request-progress"),
  requestStatusText: $("#request-status-text"),
  uploadTemplate: $("#upload-field-template"),
};

const state = {
  capabilityId: "verify-images",
  files: new Map(),
  metadata: new Map(),
  previewUrls: new Map(),
  derivedUrl: null,
  csrfToken: null,
  csrfExpiresAt: 0,
  ready: false,
  capabilities: {},
  running: false,
  notificationTimer: null,
};

function setText(node, value) {
  if (node) node.textContent = value == null ? "—" : String(value);
}

function formatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 ** 2).toFixed(1)} MiB`;
}

function notify(message, tone = "neutral", sticky = false) {
  if (!nodes.notification) return;
  window.clearTimeout(state.notificationTimer);
  nodes.notification.textContent = message;
  nodes.notification.dataset.tone = tone;
  nodes.notification.hidden = false;
  if (!sticky) {
    state.notificationTimer = window.setTimeout(() => {
      nodes.notification.hidden = true;
    }, 5200);
  }
}

function revokeUrl(url) {
  if (url) URL.revokeObjectURL(url);
}

function revokePreview(fieldName) {
  revokeUrl(state.previewUrls.get(fieldName));
  state.previewUrls.delete(fieldName);
}

function revokeAllPreviews() {
  for (const url of state.previewUrls.values()) revokeUrl(url);
  state.previewUrls.clear();
}

function revokeDerivedImage() {
  revokeUrl(state.derivedUrl);
  state.derivedUrl = null;
  if (nodes.derivedImage) nodes.derivedImage.removeAttribute("src");
  if (nodes.downloadResult) {
    nodes.downloadResult.removeAttribute("href");
    nodes.downloadResult.hidden = true;
  }
}

function capabilityContent(id) {
  return CONTENT[id] ?? CONTENT["verify-images"];
}

function currentCapability() {
  return getCapability(state.capabilityId) ?? CAPABILITIES[0];
}

function currentCapabilityReady() {
  const key = READINESS_KEYS[state.capabilityId];
  return isCapabilityReady(state.capabilities, key);
}

function fieldLimitLabel(capability, field) {
  const limits = capability.limits ?? {};
  if (field.kind === "video") {
    return `视频 ≤ ${formatBytes(limits.maxVideoBytes)} · ≤ ${limits.maxVideoDurationSeconds ?? 600} 秒`;
  }
  const megapixels = Number(limits.maxImagePixels ?? 25_000_000) / 1_000_000;
  return `JPEG / PNG · ≤ ${formatBytes(limits.maxImageBytes)} · ≤ ${megapixels} MP`;
}

function acceptedLabel(field) {
  return field.kind === "video" ? "点击或拖放视频" : "点击或拖放照片";
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}

function setFieldError(fieldName, message = "") {
  const host = document.querySelector(`[data-upload-field][data-field-name="${CSS.escape(fieldName)}"]`);
  if (!host) return;
  const error = host.querySelector("[data-field-error]");
  host.dataset.invalid = message ? "true" : "false";
  if (error) {
    error.textContent = message;
    error.hidden = !message;
  }
}

function renderFilePreview(field, file, metadata) {
  const host = document.querySelector(`[data-upload-field][data-field-name="${CSS.escape(field.name)}"]`);
  if (!host) return;
  const preview = host.querySelector("[data-file-preview]");
  const prompt = host.querySelector("[data-drop-zone]");
  const selection = host.querySelector("[data-file-selection]");
  const loadedLabel = host.querySelector("[data-file-name]");
  const meta = host.querySelector("[data-file-meta]");
  if (!preview || !prompt || !selection || !loadedLabel || !meta) return;

  revokePreview(field.name);
  const url = URL.createObjectURL(file);
  state.previewUrls.set(field.name, url);
  preview.replaceChildren();

  if (field.kind === "video") {
    const media = document.createElement("video");
    media.src = url;
    media.muted = true;
    media.playsInline = true;
    media.preload = "metadata";
    media.controls = true;
    media.setAttribute("aria-label", `${field.label}预览`);
    preview.append(media);
  } else {
    const image = document.createElement("img");
    image.src = url;
    image.alt = `${field.label}预览`;
    preview.append(image);
  }

  const facts = ["已载入", formatBytes(file.size)];
  if (metadata?.width && metadata?.height) facts.push(`${metadata.width} × ${metadata.height}`);
  if (metadata?.durationSeconds) facts.push(`${metadata.durationSeconds.toFixed(1)} 秒`);
  loadedLabel.textContent = `${field.label}已载入`;
  meta.textContent = facts.join(" · ");
  prompt.hidden = true;
  selection.hidden = false;
  host.dataset.loaded = "true";
}

async function inspectImage(file) {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(file);
    try {
      return { width: bitmap.width, height: bitmap.height };
    } finally {
      bitmap.close();
    }
  }
  const url = URL.createObjectURL(file);
  try {
    return await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => reject(new Error("无法读取图片尺寸"));
      image.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function inspectVideo(file) {
  const url = URL.createObjectURL(file);
  try {
    return await new Promise((resolve, reject) => {
      const video = document.createElement("video");
      const cleanup = () => {
        video.removeAttribute("src");
        video.load();
      };
      video.preload = "metadata";
      video.onloadedmetadata = () => {
        const durationSeconds = Number(video.duration);
        const result = {
          durationSeconds: Number.isFinite(durationSeconds) ? durationSeconds : null,
          width: video.videoWidth || null,
          height: video.videoHeight || null,
        };
        cleanup();
        resolve(result);
      };
      video.onerror = () => {
        cleanup();
        reject(new Error("无法读取视频元数据"));
      };
      video.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

function selectionForValidation() {
  const selection = {};
  for (const [name, file] of state.files.entries()) {
    selection[name] = { file, ...(state.metadata.get(name) ?? {}) };
  }
  return selection;
}

function updateRunState() {
  const validation = validateSelection(state.capabilityId, selectionForValidation());
  const ready = currentCapabilityReady();
  if (nodes.runButton) {
    nodes.runButton.disabled = state.running || !validation.valid || !ready;
    nodes.runButton.setAttribute("aria-busy", state.running ? "true" : "false");
  }
  if (!state.running && nodes.requestStatusText) {
    if (!ready) setText(nodes.requestStatusText, "等待当前算法能力就绪");
    else if (validation.valid) setText(nodes.requestStatusText, "素材已就绪，可以运行算法");
    else setText(nodes.requestStatusText, "选择全部必填素材后即可运行");
  }
  return validation;
}

async function handleSelectedFile(field, file) {
  setFieldError(field.name, "");
  if (!file) return;
  state.files.set(field.name, file);
  state.metadata.delete(field.name);

  try {
    const metadata = field.kind === "video" ? await inspectVideo(file) : await inspectImage(file);
    if (state.files.get(field.name) !== file) return;
    state.metadata.set(field.name, metadata);
    renderFilePreview(field, file, metadata);
  } catch (error) {
    if (state.files.get(field.name) !== file) return;
    renderFilePreview(field, file, null);
    setFieldError(field.name, error instanceof Error ? error.message : "无法读取素材");
  }

  const validation = updateRunState();
  const issue = validation.errors.find((item) => item.field === field.name);
  if (issue) setFieldError(field.name, issue.message);
  if (validation.warnings.length) notify(validation.warnings[0].message, "warning");
}

function clearField(fieldName) {
  state.files.delete(fieldName);
  state.metadata.delete(fieldName);
  revokePreview(fieldName);
  const host = document.querySelector(`[data-upload-field][data-field-name="${CSS.escape(fieldName)}"]`);
  if (host) {
    const input = host.querySelector("input[type=file]");
    const preview = host.querySelector("[data-file-preview]");
    const selection = host.querySelector("[data-file-selection]");
    const prompt = host.querySelector("[data-drop-zone]");
    if (input) input.value = "";
    if (preview) {
      preview.replaceChildren();
    }
    if (selection) selection.hidden = true;
    if (prompt) prompt.hidden = false;
    host.dataset.loaded = "false";
  }
  setFieldError(fieldName, "");
  updateRunState();
}

function buildUploadCard(capability, field, index) {
  if (!nodes.uploadTemplate) throw new Error("上传组件模板缺失");
  const fragment = nodes.uploadTemplate.content.cloneNode(true);
  const card = fragment.querySelector("[data-upload-field]");
  const fieldIndex = fragment.querySelector("[data-field-index]");
  const fieldTitle = fragment.querySelector("[data-field-title]");
  const fieldRequirement = fragment.querySelector("[data-field-requirement]");
  const drop = fragment.querySelector("[data-drop-zone]");
  const input = fragment.querySelector("[data-file-input]");
  const clearButton = fragment.querySelector("[data-clear-file]");
  if (!card || !fieldIndex || !fieldTitle || !fieldRequirement || !drop || !input || !clearButton) {
    throw new Error("上传组件模板不完整");
  }
  card.dataset.fieldName = field.name;
  card.dataset.loaded = "false";
  card.dataset.invalid = "false";
  fieldIndex.textContent = String(index + 1).padStart(2, "0");
  fieldTitle.textContent = field.label;
  fieldRequirement.textContent = fieldLimitLabel(capability, field);
  clearButton.addEventListener("click", () => clearField(field.name));
  input.id = `upload-${field.name}`;
  input.name = field.name;
  input.accept = field.accept;
  input.required = field.required !== false;
  input.addEventListener("change", () => handleSelectedFile(field, input.files?.[0]));
  drop.tabIndex = 0;
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });
  for (const eventName of ["dragenter", "dragover"]) {
    drop.addEventListener(eventName, (event) => {
      event.preventDefault();
      drop.dataset.dragActive = "true";
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    drop.addEventListener(eventName, (event) => {
      event.preventDefault();
      drop.dataset.dragActive = "false";
    });
  }
  drop.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    handleSelectedFile(field, file);
  });
  const title = drop.querySelector("[data-drop-title]");
  if (title) title.textContent = acceptedLabel(field);
  return fragment;
}

function renderInputs() {
  if (!nodes.inputFields) return;
  const capability = currentCapability();
  nodes.inputFields.replaceChildren();
  nodes.inputFields.dataset.layout = capability.fields.length > 1 ? "paired" : "single";
  capability.fields.forEach((field, index) => nodes.inputFields.append(buildUploadCard(capability, field, index)));

  if (state.capabilityId === "verify-video-full") {
    const warning = makeElement(
      "div",
      "full-video-warning",
      "完整核验为同步高耗时测试，最长可能等待 10 分钟；页面不会自动重试。",
    );
    warning.setAttribute("role", "note");
    nodes.inputFields.append(warning);
  }
  updateRunState();
}

function clearResult() {
  revokeDerivedImage();
  renderEvidenceFrames(nodes.evidenceBody, {});
  if (nodes.evidencePanel) nodes.evidencePanel.hidden = true;
  if (nodes.resultEmpty) nodes.resultEmpty.hidden = false;
  if (nodes.resultContent) nodes.resultContent.hidden = true;
  if (nodes.resultMetrics) nodes.resultMetrics.replaceChildren();
  if (nodes.resultReasons) nodes.resultReasons.replaceChildren();
  if (nodes.derivedPanel) nodes.derivedPanel.hidden = true;
  if (nodes.resultSummary) nodes.resultSummary.dataset.suggestion = "REVIEW";
  setText(nodes.resultTraceId, "—");
  setText(nodes.resultDuration, "");
  if (nodes.rawJson) nodes.rawJson.textContent = "尚未运行测试";
}

function clearWorkspace() {
  revokeAllPreviews();
  state.files.clear();
  state.metadata.clear();
  clearResult();
}

function updateWorkspaceCopy(capability) {
  const content = capabilityContent(capability.id);
  setText(nodes.workspaceEyebrow, content.eyebrow);
  setText(nodes.workspaceTitle, content.title);
  setText(nodes.workspaceDescription, content.description);
  setText(nodes.endpointLabel, capability.endpoint);
  setText(nodes.timeoutLabel, `${Math.round(capability.timeoutMs / 1000)} 秒截止`);
  if (nodes.inputRequirements) {
    const descriptions = capability.fields.map((field) => fieldLimitLabel(capability, field));
    setText(nodes.inputRequirements, [...new Set(descriptions)].join("；"));
  }
  if (nodes.runButton) {
    const label = nodes.runButton.querySelector("span:first-child");
    setText(label ?? nodes.runButton, content.action);
  }
}

function activateCapability(id, announce = true) {
  if (state.running || !getCapability(id)) return;
  clearWorkspace();
  state.capabilityId = getCapability(id).id;
  document.querySelectorAll(".capability-button").forEach((button) => {
    const active = getCapability(button.dataset.capability)?.id === state.capabilityId;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  updateWorkspaceCopy(currentCapability());
  renderInputs();
  if (announce) notify(`已切换到${capabilityContent(state.capabilityId).title}`, "neutral");
}

function renderMetrics(details) {
  if (!nodes.resultMetrics) return;
  nodes.resultMetrics.replaceChildren();
  for (const detail of details) {
    const item = makeElement("div", "result-metric");
    item.dataset.metric = detail.key;
    item.append(makeElement("dt", "", detail.label), makeElement("dd", "", detail.value));
    nodes.resultMetrics.append(item);
  }
}

function renderDerivedImage(payload) {
  revokeDerivedImage();
  if (!nodes.derivedPanel || !nodes.derivedImage || !nodes.downloadResult) return;
  if (typeof payload.image_base64 !== "string" || !payload.image_base64) {
    nodes.derivedPanel.hidden = true;
    return;
  }
  const format = String(payload.output_format ?? "jpeg").toLowerCase() === "png" ? "png" : "jpeg";
  const mime = format === "png" ? "image/png" : "image/jpeg";
  const blob = dataUrlToBlob(`data:${mime};base64,${payload.image_base64}`);
  state.derivedUrl = URL.createObjectURL(blob);
  nodes.derivedImage.src = state.derivedUrl;
  nodes.derivedImage.alt = state.capabilityId === "portrait" ? "生成的白底头肩照" : "背景虚化后的见证照";
  nodes.downloadResult.href = state.derivedUrl;
  nodes.downloadResult.download = state.capabilityId === "portrait" ? `portrait-result.${format === "png" ? "png" : "jpg"}` : "witness-blur-result.jpg";
  nodes.downloadResult.hidden = false;
  nodes.derivedPanel.hidden = false;
}

function renderResult(payload, httpStatus, elapsedMs) {
  if (nodes.resultEmpty) nodes.resultEmpty.hidden = true;
  if (nodes.resultContent) nodes.resultContent.hidden = false;

  const presence = currentCapability().responseKind === "video-presence";
  const view = getSuggestionView({ ...payload, ...(presence ? { mode: "presence" } : {}),
    ...(httpStatus && (httpStatus < 200 || httpStatus >= 300) ? { success: false } : {}) });
  const suggestion = String(payload.suggestion ?? "ERROR").toUpperCase();
  const displaySuggestion = presence ? "INCONCLUSIVE"
    : suggestion === "PASS" && !view.canTreatAsPass ? "REVIEW" : ["PASS", "RECAPTURE", "REVIEW"].includes(suggestion) ? suggestion : "ERROR";
  if (nodes.resultSummary) nodes.resultSummary.dataset.suggestion = presence ? "REVIEW" : displaySuggestion;
  setText(nodes.resultStatus, displaySuggestion);
  setText(nodes.resultSuggestion, view.label);

  const notices = [...view.notices];
  if (httpStatus && httpStatus !== 200) notices.unshift(`HTTP ${httpStatus}`);
  if (Number.isFinite(elapsedMs)) notices.push(`浏览器往返 ${(elapsedMs / 1000).toFixed(2)} 秒`);
  const reasons = translateReasons(payload.reason_codes);
  setText(nodes.resultMessage, [view.message, ...notices].filter(Boolean).join("；"));
  if (nodes.resultReasons) {
    nodes.resultReasons.replaceChildren();
    for (const reason of reasons) nodes.resultReasons.append(makeElement("li", "", reason));
  }
  setText(nodes.resultDuration, Number.isFinite(elapsedMs) ? `${(elapsedMs / 1000).toFixed(2)} s` : "");
  const trace = typeof payload.trace_id === "string" && payload.trace_id ? `${payload.trace_id.slice(0, 8)}…` : "—";
  setText(nodes.resultTraceId, trace);

  renderMetrics(buildResultDetails(state.capabilityId, payload));
  if (nodes.evidencePanel) nodes.evidencePanel.hidden = !presence;
  renderEvidenceFrames(nodes.evidenceBody, presence ? payload : {});
  try {
    renderDerivedImage(payload);
  } catch {
    if (nodes.derivedPanel) nodes.derivedPanel.hidden = true;
    notify("派生图返回格式异常，结构化结果仍可查看。", "warning");
  }
  if (nodes.rawJson) nodes.rawJson.textContent = safeJsonText(payload);
}

function renderRequestFailure(message, payload = {}) {
  const safePayload = payload && typeof payload === "object" ? payload : { message };
  renderResult(
    { success: false, suggestion: "REVIEW", reason_codes: ["INFERENCE_ERROR"], message, ...safePayload },
    Number(safePayload.http_status) || null,
    null,
  );
}

function setRunning(running) {
  state.running = running;
  document.body.dataset.running = running ? "true" : "false";
  document.querySelectorAll(".capability-button").forEach((button) => {
    button.disabled = running || button.dataset.available === "false";
  });
  if (nodes.resetButton) nodes.resetButton.disabled = running;
  if (nodes.runButton) {
    nodes.runButton.classList.toggle("is-running", running);
    const label = nodes.runButton.querySelector("span:first-child");
    setText(label ?? nodes.runButton, running ? "算法处理中…" : capabilityContent(state.capabilityId).action);
  }
  if (nodes.requestProgress) nodes.requestProgress.hidden = !running;
  if (nodes.inputFields) nodes.inputFields.setAttribute("aria-busy", running ? "true" : "false");
  if (running) setText(nodes.requestStatusText, "请求已提交，正在等待算法结果");
  updateRunState();
}

async function getCsrfToken() {
  if (state.csrfToken && Date.now() < state.csrfExpiresAt - 30_000) return state.csrfToken;
  const response = await fetch("/api/console/session", {
    method: "GET",
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
  });
  if (!response.ok) throw new Error("无法建立安全测试会话");
  const payload = await response.json();
  const token = payload.csrf_token ?? payload.token;
  if (typeof token !== "string" || token.length < 32) throw new Error("测试会话响应无效");
  const ttlSeconds = Number(payload.expires_in_seconds ?? payload.ttl_seconds ?? 900);
  state.csrfToken = token;
  state.csrfExpiresAt = Date.now() + Math.max(60, Math.min(ttlSeconds, 900)) * 1000;
  return token;
}

async function parseResponse(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { message: "服务返回了无法解析的响应" };
  }
}

async function runCurrentTest(event) {
  event.preventDefault();
  if (state.running) return;
  const validation = updateRunState();
  if (!validation.valid) {
    const first = validation.errors[0];
    if (first?.field) setFieldError(first.field, first.message);
    notify(first?.message ?? "请先补齐测试素材。", "danger");
    return;
  }
  if (!currentCapabilityReady()) {
    notify("当前算法能力尚未就绪，请刷新服务状态。", "danger");
    return;
  }

  clearResult();
  setRunning(true);
  const capability = currentCapability();
  notify(`正在运行${capabilityContent(capability.id).title}，请保持页面打开。`, "neutral", true);
  const started = performance.now();

  try {
    const csrf = await getCsrfToken();
    const formData = new FormData();
    for (const field of capability.fields) {
      const file = state.files.get(field.name);
      formData.append(field.name, file, safeUploadFilename(field, file));
    }
    const response = await fetch(capability.endpoint, {
      method: "POST",
      headers: { "X-Visual-Console-CSRF": csrf },
      body: formData,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      signal: AbortSignal.timeout(capability.timeoutMs + 30_000),
    });
    const payload = await parseResponse(response);
    const elapsed = performance.now() - started;
    if (response.status === 403) {
      state.csrfToken = null;
      state.csrfExpiresAt = 0;
    }
    renderResult(payload, response.status, elapsed);
    notify(response.ok ? "算法请求已完成，请查看右侧建议。" : `请求失败（HTTP ${response.status}），请查看原因。`, response.ok ? "success" : "danger");
  } catch (error) {
    const message = error instanceof Error ? error.message : "无法连接测试台服务";
    renderRequestFailure(message);
    notify(message, "danger", true);
  } finally {
    setRunning(false);
  }
}

function modelCounts(payload) {
  const ready = Number(payload.models_ready ?? payload.models?.ready ?? payload.model_count?.ready ?? 0);
  const total = Number(payload.models_total ?? payload.models?.total ?? payload.model_count?.total ?? 0);
  return { ready, total };
}

function applyReadiness(payload) {
  state.ready = payload.ready === true;
  state.capabilities = payload.capabilities && typeof payload.capabilities === "object" ? payload.capabilities : {};
  const counts = modelCounts(payload);
  const capabilityValues = CAPABILITIES.map((capability) => state.capabilities[READINESS_KEYS[capability.id]]);
  const capabilityReadyCount = capabilityValues.filter((value) => value === "ready").length;

  if (nodes.serviceStatusContainer) nodes.serviceStatusContainer.dataset.state = state.ready ? "ready" : "offline";
  setText(nodes.serviceStatus, state.ready ? "服务正常" : "服务未就绪");
  setText(nodes.readyModelCount, `${counts.ready} / ${counts.total}`);
  setText(nodes.readyCapabilityCount, `${capabilityReadyCount} / ${capabilityValues.length}`);
  const checkedAt = payload.checked_at ? new Date(payload.checked_at) : new Date();
  setText(nodes.readinessCheckedAt, Number.isNaN(checkedAt.getTime()) ? "刚刚" : checkedAt.toLocaleTimeString("zh-CN", { hour12: false }));

  document.querySelectorAll(".capability-button").forEach((button) => {
    const key = button.dataset.readinessKey ?? READINESS_KEYS[button.dataset.capability];
    const available = isCapabilityReady(state.capabilities, key);
    button.dataset.available = available ? "true" : "false";
    button.disabled = state.running || !available;
    const indicator = button.querySelector(".capability-state");
    if (indicator) {
      indicator.dataset.state = available ? "ready" : "unavailable";
      const label = indicator.querySelector("[data-capability-status-label]");
      setText(label, available ? "就绪" : "不可用");
    }
  });
  updateRunState();
}

async function refreshReadiness({ announce = false } = {}) {
  if (nodes.refreshReadiness) {
    nodes.refreshReadiness.disabled = true;
    nodes.refreshReadiness.setAttribute("aria-busy", "true");
  }
  try {
    const response = await fetch("/api/console/ready", {
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
    });
    const payload = await parseResponse(response);
    if (response.status !== 200 && response.status !== 503) {
      throw new Error(payload.message ?? `readiness HTTP ${response.status}`);
    }
    applyReadiness(payload);
    if (announce) notify(response.ok ? "服务状态已刷新。" : "算法服务当前未就绪。", response.ok ? "success" : "warning");
  } catch {
    applyReadiness({ ready: false, capabilities: {}, checked_at: new Date().toISOString() });
    notify("无法读取算法 readiness，请确认 18002 服务正常。", "danger", true);
  } finally {
    if (nodes.refreshReadiness) {
      nodes.refreshReadiness.disabled = false;
      nodes.refreshReadiness.setAttribute("aria-busy", "false");
    }
  }
}

function resetCurrent() {
  if (state.running) return;
  clearWorkspace();
  renderInputs();
  notify("当前测试素材和结果已从浏览器内存清空。", "neutral");
}

function bindEvents() {
  document.querySelectorAll(".capability-button").forEach((button) => {
    button.addEventListener("click", () => activateCapability(button.dataset.capability));
  });
  nodes.form?.addEventListener("submit", runCurrentTest);
  nodes.resetButton?.addEventListener("click", resetCurrent);
  nodes.refreshReadiness?.addEventListener("click", () => refreshReadiness({ announce: true }));
  window.addEventListener("beforeunload", () => {
    revokeAllPreviews();
    revokeDerivedImage();
    state.files.clear();
    state.metadata.clear();
    state.csrfToken = null;
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !state.running) refreshReadiness();
  });
}

bindEvents();
activateCapability("verify-images", false);
refreshReadiness();
window.setInterval(() => {
  if (!document.hidden && !state.running) refreshReadiness();
}, 60_000);
