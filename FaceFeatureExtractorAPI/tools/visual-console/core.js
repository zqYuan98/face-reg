const MIB = 1024 * 1024;
const MULTIPART_OVERHEAD_BYTES = 64 * 1024;

export const EXPECTED_FEATURE_DIMENSION = 1024;

const IMAGE_ACCEPT = "image/jpeg,image/png,.jpg,.jpeg,.png";
const VIDEO_ACCEPT = "video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,video/webm,.mp4,.mov,.avi,.mkv,.webm,.m4v";

const SAFE_UPLOAD_EXTENSIONS = Object.freeze({
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "video/mp4": ".mp4",
  "video/quicktime": ".mov",
  "video/x-msvideo": ".avi",
  "video/x-matroska": ".mkv",
  "video/webm": ".webm",
});

const SAFE_KIND_EXTENSIONS = Object.freeze({
  image: new Set([".jpg", ".jpeg", ".png"]),
  video: new Set([".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]),
});

function freezeFields(fields) {
  return Object.freeze(fields.map((field) => Object.freeze({ ...field })));
}

function freezeCapability(capability) {
  return Object.freeze({
    ...capability,
    fields: freezeFields(capability.fields),
    limits: Object.freeze({ ...capability.limits }),
  });
}

const imageField = (name, label) => ({
  name,
  label,
  kind: "image",
  accept: IMAGE_ACCEPT,
  required: true,
});

const videoField = (name, label) => ({
  name,
  label,
  kind: "video",
  accept: VIDEO_ACCEPT,
  required: true,
});

const IMAGE_BYTES = 15 * MIB;
const IMAGE_PIXELS = 25_000_000;
const WITNESS_PIXELS = 16_000_000;
const VIDEO_BYTES = 500 * MIB;

/**
 * Frozen public contract consumed by the visual console.
 * Limits mirror the deployed API and apply per declared media field unless noted.
 */
export const CAPABILITIES = Object.freeze([
  freezeCapability({
    id: "portrait",
    label: "白底头肩照",
    description: "生成标准白底头肩照",
    endpoint: "/api/v2/portrait/generate",
    method: "POST",
    timeoutMs: 30_000,
    responseKind: "derived-image",
    fields: [imageField("image", "原始照片")],
    limits: {
      maxImageBytes: IMAGE_BYTES,
      maxImagePixels: IMAGE_PIXELS,
      maxMultipartBytes: IMAGE_BYTES + MULTIPART_OVERHEAD_BYTES,
    },
  }),
  freezeCapability({
    id: "witness",
    label: "见证照背景虚化",
    description: "优先保护人物，可信证卡作为额外保护，并虚化其余背景",
    endpoint: "/api/v2/witness/blur-background",
    method: "POST",
    timeoutMs: 30_000,
    responseKind: "derived-image",
    fields: [imageField("image", "见证照片")],
    limits: {
      maxImageBytes: IMAGE_BYTES,
      maxImagePixels: WITNESS_PIXELS,
      maxMultipartBytes: IMAGE_BYTES + MULTIPART_OVERHEAD_BYTES,
    },
  }),
  freezeCapability({
    id: "verify-images",
    label: "双图片身份核验",
    description: "可信参考照与待核验照片一比一比对",
    endpoint: "/api/v2/verify/images",
    method: "POST",
    timeoutMs: 30_000,
    responseKind: "image-verification",
    fields: [
      imageField("reference_image", "可信参考照片"),
      imageField("probe_image", "待核验照片"),
    ],
    limits: {
      maxImageBytes: IMAGE_BYTES,
      maxImagePixels: IMAGE_PIXELS,
      maxMultipartBytes: 2 * IMAGE_BYTES + MULTIPART_OVERHEAD_BYTES,
    },
  }),
  freezeCapability({
    id: "verify-video-quick",
    label: "视频快速核验",
    description: "提交前抽帧快速检查",
    endpoint: "/api/v2/verify/video/quick",
    method: "POST",
    timeoutMs: 60_000,
    responseKind: "video-verification",
    fields: [
      imageField("reference_image", "可信参考照片"),
      videoField("video", "签署视频"),
    ],
    limits: {
      maxImageBytes: IMAGE_BYTES,
      maxImagePixels: IMAGE_PIXELS,
      maxVideoBytes: VIDEO_BYTES,
      maxVideoDurationSeconds: 600,
      maxVideoFps: 120,
      maxMultipartBytes: IMAGE_BYTES + VIDEO_BYTES + MULTIPART_OVERHEAD_BYTES,
    },
  }),
  freezeCapability({
    id: "verify-video-full",
    label: "视频完整核验",
    description: "整段视频同步分析；正式业务应由异步 worker 调用",
    endpoint: "/api/v2/verify/video/full",
    method: "POST",
    timeoutMs: 600_000,
    responseKind: "video-verification",
    fields: [
      imageField("reference_image", "可信参考照片"),
      videoField("video", "签署视频"),
    ],
    limits: {
      maxImageBytes: IMAGE_BYTES,
      maxImagePixels: IMAGE_PIXELS,
      maxVideoBytes: VIDEO_BYTES,
      maxVideoDurationSeconds: 600,
      maxVideoFps: 120,
      maxAnalyzedFrames: 1_200,
      maxMultipartBytes: IMAGE_BYTES + VIDEO_BYTES + MULTIPART_OVERHEAD_BYTES,
    },
  }),
]);

/**
 * Returns a deterministic multipart filename so a customer-provided local
 * basename is never forwarded to the algorithm service or its access logs.
 */
export function safeUploadFilename(field, file) {
  const kind = field?.kind === "video" ? "video" : "image";
  const mimeExtension = SAFE_UPLOAD_EXTENSIONS[String(file?.type ?? "").toLowerCase()];
  const originalName = String(file?.name ?? "").toLowerCase();
  const originalExtension = originalName.includes(".") ? originalName.slice(originalName.lastIndexOf(".")) : "";
  const extension = mimeExtension
    ?? (SAFE_KIND_EXTENSIONS[kind].has(originalExtension) ? originalExtension : kind === "video" ? ".mp4" : ".jpg");
  return `visual-console-${kind}${extension === ".jpeg" ? ".jpg" : extension}`;
}

const CAPABILITIES_BY_ID = new Map(CAPABILITIES.map((item) => [item.id, item]));
const CAPABILITY_ALIASES = new Map([
  ["portrait-generate", "portrait"],
  ["witness-blur", "witness"],
  ["witness_blur", "witness"],
  ["images", "verify-images"],
  ["image-verify", "verify-images"],
  ["verify_images", "verify-images"],
  ["image_verification", "verify-images"],
  ["quick", "verify-video-quick"],
  ["video-quick", "verify-video-quick"],
  ["video_quick", "verify-video-quick"],
  ["verify_video_quick", "verify-video-quick"],
  ["full", "verify-video-full"],
  ["video-full", "verify-video-full"],
  ["video_full", "verify-video-full"],
  ["verify_video_full", "verify-video-full"],
  ...CAPABILITIES.map((item) => [item.endpoint, item.id]),
]);

export const REASON_LABELS = Object.freeze({
  INVALID_IMAGE: "图片无效",
  IMAGE_TOO_LARGE: "图片超过限制",
  UNSUPPORTED_FORMAT: "媒体格式不支持",
  NO_FACE: "未检测到人脸",
  MULTIPLE_FACES: "检测到多张人脸",
  FACE_TOO_SMALL: "人脸区域过小",
  FACE_CROPPED: "人脸被画面裁切",
  TOO_DARK: "画面过暗",
  TOO_BRIGHT: "画面过亮",
  TOO_BLURRY: "画面过于模糊",
  BAD_POSE: "人脸姿态不符合要求",
  LANDMARK_FAILED: "人脸关键点定位失败",
  ALIGNMENT_FAILED: "人脸对齐失败",
  FEATURE_FAILED: "人脸特征提取失败",
  FEATURE_VERSION_MISMATCH: "特征版本不匹配",
  THRESHOLD_UNCALIBRATED: "身份阈值尚未标定",
  MATTING_FAILED: "人像分割失败",
  MATTING_LOW_CONFIDENCE: "人像分割可信度不足",
  SUBJECT_CROPPED: "人物主体被裁切",
  SHOULDERS_NOT_VISIBLE: "肩部区域不可见",
  PORTRAIT_GEOMETRY_INVALID: "头肩照构图不符合要求",
  CARD_NOT_FOUND: "未检测到证卡",
  CARD_TOO_SMALL: "证卡区域过小",
  CARD_CROPPED: "证卡被画面裁切",
  CARD_DETECTION_UNCERTAIN: "证卡检测结果不确定",
  INVALID_VIDEO: "视频无效",
  VIDEO_TOO_LARGE: "视频超过限制",
  VIDEO_TOO_LONG: "视频时长超过限制",
  VIDEO_DECODE_FAILED: "视频解码失败",
  INSUFFICIENT_VALID_FRAMES: "有效视频帧不足",
  MULTIPLE_SUBJECTS: "视频中存在多名稳定人物",
  SUBJECT_SWITCH: "视频人物发生切换",
  SUBJECT_SWITCHED: "视频人物发生切换",
  CALIBRATION_REQUIRED: "缺少有效的签名校准",
  IDENTITY_UNCERTAIN: "身份一致性不确定",
  MODEL_UNAVAILABLE: "所需模型不可用",
  INFERENCE_ERROR: "算法处理异常",
  ALGORITHM_TIMEOUT: "算法处理超时",
  ACCESS_DENIED: "访问被拒绝",
});

export function getCapability(idOrCapability) {
  if (idOrCapability && typeof idOrCapability === "object") {
    return getCapability(idOrCapability.id);
  }
  if (typeof idOrCapability !== "string") {
    return null;
  }
  const raw = idOrCapability.trim();
  if (!raw) {
    return null;
  }
  const normalized = raw.toLowerCase();
  const canonical = CAPABILITY_ALIASES.get(normalized) ?? normalized;
  return CAPABILITIES_BY_ID.get(canonical) ?? null;
}

function issue(field, code, message) {
  return Object.freeze({ field, code, message });
}

function selectionValue(selection, name) {
  if (!selection) {
    return null;
  }
  if (typeof FormData !== "undefined" && selection instanceof FormData) {
    return selection.get(name);
  }
  if (selection instanceof Map) {
    return selection.get(name);
  }
  return typeof selection === "object" ? selection[name] : null;
}

function mediaDescriptor(value) {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (Object.prototype.hasOwnProperty.call(value, "file")) {
    if (!value.file || typeof value.file !== "object") {
      return null;
    }
    return { ...value, file: value.file };
  }
  return { file: value };
}

function firstFinite(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) {
      return number;
    }
  }
  return null;
}

function mediaExtension(name) {
  if (typeof name !== "string") {
    return "";
  }
  const match = /\.([a-z0-9]+)$/i.exec(name.trim());
  return match ? `.${match[1].toLowerCase()}` : "";
}

function mediaTypeAllowed(kind, type, name) {
  const normalizedType = typeof type === "string" ? type.toLowerCase().split(";", 1)[0] : "";
  const extension = mediaExtension(name);
  if (kind === "image") {
    if (normalizedType && normalizedType !== "application/octet-stream") {
      return normalizedType === "image/jpeg" || normalizedType === "image/png";
    }
    return [".jpg", ".jpeg", ".png"].includes(extension);
  }
  if (normalizedType && normalizedType !== "application/octet-stream") {
    return normalizedType.startsWith("video/") || normalizedType === "application/x-matroska";
  }
  return (
    [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"].includes(extension)
  );
}

/**
 * Synchronously validates File-like selections. Pixel/duration/FPS checks run
 * when callers provide metadata as {file, width, height, durationSeconds, fps}.
 */
export function validateSelection(capabilityId, selection = {}) {
  const capability = getCapability(capabilityId);
  const errors = [];
  const warnings = [];
  if (!capability) {
    errors.push(issue("capability", "UNKNOWN_CAPABILITY", "未知测试能力"));
    return Object.freeze({ valid: false, errors, warnings, capability: null });
  }

  let selectedBytes = 0;
  for (const field of capability.fields) {
    const descriptor = mediaDescriptor(selectionValue(selection, field.name));
    if (!descriptor) {
      if (field.required) {
        errors.push(issue(field.name, "REQUIRED", `请选择${field.label}`));
      }
      continue;
    }

    const { file } = descriptor;
    const size = firstFinite(descriptor.size, file.size);
    const type = descriptor.type ?? file.type;
    const name = descriptor.name ?? file.name;
    if (size === null || size < 0) {
      errors.push(issue(field.name, "INVALID_SIZE", `${field.label}大小无效`));
    } else {
      selectedBytes += size;
      const byteLimit = field.kind === "video"
        ? capability.limits.maxVideoBytes
        : capability.limits.maxImageBytes;
      if (Number.isFinite(byteLimit) && size > byteLimit) {
        const code = field.kind === "video" ? "VIDEO_TOO_LARGE" : "IMAGE_TOO_LARGE";
        errors.push(issue(field.name, code, `${field.label}超过${formatBytes(byteLimit)}限制`));
      }
    }

    if (!mediaTypeAllowed(field.kind, type, name)) {
      errors.push(issue(field.name, "UNSUPPORTED_FORMAT", `${field.label}格式不受支持`));
    }

    if (field.kind === "image") {
      const width = firstFinite(descriptor.width, file.width);
      const height = firstFinite(descriptor.height, file.height);
      if (width !== null || height !== null) {
        if (!(width > 0 && height > 0)) {
          errors.push(issue(field.name, "INVALID_DIMENSIONS", `${field.label}尺寸无效`));
        } else if (width * height > capability.limits.maxImagePixels) {
          errors.push(
            issue(
              field.name,
              "IMAGE_TOO_LARGE",
              `${field.label}超过${formatNumber(capability.limits.maxImagePixels)}像素限制`,
            ),
          );
        }
      } else {
        warnings.push(issue(field.name, "IMAGE_METADATA_REQUIRED", `${field.label}尚未校验像素尺寸`));
      }
    } else {
      const duration = firstFinite(descriptor.durationSeconds, file.durationSeconds, file.duration);
      const fps = firstFinite(descriptor.fps, file.fps);
      if (duration !== null && (!(duration > 0) || duration > capability.limits.maxVideoDurationSeconds)) {
        errors.push(issue(field.name, "VIDEO_TOO_LONG", `${field.label}时长必须在600秒以内`));
      } else if (duration === null) {
        warnings.push(issue(field.name, "VIDEO_METADATA_REQUIRED", `${field.label}尚未校验时长`));
      }
      if (fps !== null && (!(fps > 0) || fps > capability.limits.maxVideoFps)) {
        errors.push(issue(field.name, "VIDEO_FPS_TOO_HIGH", `${field.label}帧率必须在120 FPS以内`));
      } else if (fps === null) {
        warnings.push(issue(field.name, "VIDEO_FPS_REQUIRED", `${field.label}尚未校验帧率`));
      }
    }
  }

  if (
    Number.isFinite(capability.limits.maxMultipartBytes) &&
    selectedBytes + MULTIPART_OVERHEAD_BYTES > capability.limits.maxMultipartBytes
  ) {
    errors.push(issue("request", "MULTIPART_TOO_LARGE", "所选文件合计超过请求体限制"));
  }

  return Object.freeze({
    valid: errors.length === 0,
    errors: Object.freeze(errors),
    warnings: Object.freeze(warnings),
    capability,
  });
}

function normalizedReasonCodes(value) {
  const items = typeof value === "string"
    ? [value]
    : value && typeof value[Symbol.iterator] === "function"
      ? [...value]
      : [];
  return [...new Set(items.map((item) => String(item).trim().toUpperCase()).filter(Boolean))];
}

export function translateReasons(reasonCodes) {
  return normalizedReasonCodes(reasonCodes).map(
    (code) => REASON_LABELS[code] ?? code.replaceAll("_", " "),
  );
}

function hasUncalibratedSemantics(result, reasonCodes) {
  return (
    reasonCodes.includes("THRESHOLD_UNCALIBRATED") ||
    String(result?.calibration?.status ?? "").toUpperCase() === "UNCALIBRATED"
  );
}

/** Returns a fail-closed, presentation-ready interpretation of an API result. */
export function getSuggestionView(result) {
  if (!result || typeof result !== "object") {
    return Object.freeze({
      key: "unknown",
      label: "尚无结果",
      tone: "neutral",
      message: "请先提交一次测试请求。",
      notices: Object.freeze([]),
      canTreatAsPass: false,
    });
  }

  const reasonCodes = normalizedReasonCodes(result.reason_codes);
  const reasonLabels = translateReasons(reasonCodes);
  const notices = [];
  const uncalibrated = hasUncalibratedSemantics(result, reasonCodes);
  const hasFeatureDimension = result.feature_dimension !== undefined && result.feature_dimension !== null;
  const featureDimensionMismatch = hasFeatureDimension && Number(result.feature_dimension) !== EXPECTED_FEATURE_DIMENSION;
  let suggestion = String(result.suggestion ?? "").toUpperCase();

  if (uncalibrated) {
    notices.push("阈值尚未标定，任何相似度都不能作为通过依据。");
    if (suggestion === "PASS") {
      suggestion = "REVIEW";
    }
  }
  if (result.liveness_evaluated === false) {
    notices.push("本结果未评估活体，不得显示为“活体通过”。");
  }
  if (featureDimensionMismatch) {
    notices.push(`特征维度不是契约要求的${EXPECTED_FEATURE_DIMENSION}维。`);
  }
  if (reasonLabels.length) {
    notices.push(`原因：${reasonLabels.join("、")}`);
  }

  let view;
  if (featureDimensionMismatch || (suggestion === "PASS" && result.success === false)) {
    view = {
      key: "error",
      label: "结果不可用",
      tone: "danger",
      message: featureDimensionMismatch ? "特征契约不匹配，请停止使用该结果。" : "算法流程未完成，不能按通过处理。",
    };
  } else if (suggestion === "PASS") {
    view = {
      key: "pass",
      label: "算法建议通过",
      tone: "success",
      message: "本次算法结果满足当前已标定规则；最终业务决定仍由上层系统作出。",
    };
  } else if (suggestion === "RECAPTURE") {
    view = {
      key: "recapture",
      label: "建议重新采集",
      tone: "warning",
      message: "采集质量不符合要求，请根据原因提示重新拍摄或录制。",
    };
  } else if (suggestion === "REVIEW") {
    view = {
      key: "review",
      label: "需要人工复核",
      tone: "warning",
      message: uncalibrated ? "当前阈值尚未标定，结果只能进入人工复核。" : "算法无法给出可靠通过结论，请转人工复核。",
    };
  } else if (result.success === false) {
    view = {
      key: "error",
      label: "处理未完成",
      tone: "danger",
      message: "请求或算法流程未完成，请检查错误原因。",
    };
  } else {
    view = {
      key: "unknown",
      label: "结果待确认",
      tone: "neutral",
      message: "响应未包含可识别的算法建议。",
    };
  }

  const canTreatAsPass =
    view.key === "pass" &&
    result.success !== false &&
    !uncalibrated &&
    !featureDimensionMismatch &&
    reasonCodes.length === 0;

  return Object.freeze({
    ...view,
    notices: Object.freeze(notices),
    canTreatAsPass,
  });
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) {
    return "—";
  }
  return bytes >= MIB ? `${Math.round((bytes / MIB) * 10) / 10} MiB` : `${bytes} B`;
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN").format(number) : "—";
}

function finiteText(value, digits = 2) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : null;
}

function percentText(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : null;
}

function dimensionsText(width, height) {
  const w = Number(width);
  const h = Number(height);
  return Number.isInteger(w) && w > 0 && Number.isInteger(h) && h > 0 ? `${w} × ${h}` : null;
}

function maskedIdentifier(value) {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text) {
    return null;
  }
  if (text.length <= 4) {
    return "••••";
  }
  return `${text.slice(0, 4)}…${text.slice(-4)}`;
}

function calibrationText(calibration) {
  const status = String(calibration?.status ?? "").toUpperCase();
  if (status === "ACTIVE") {
    return "已标定";
  }
  if (status === "UNCALIBRATED") {
    return "未标定（仅供复核）";
  }
  return null;
}

function sideText(side) {
  if (!side || typeof side !== "object") {
    return null;
  }
  if (!side.analyzed) {
    return "未分析";
  }
  if (side.usable) {
    return "可用";
  }
  const reasons = translateReasons(side.reason_codes);
  return reasons.length ? `不可用：${reasons.join("、")}` : "不可用";
}

/** Builds an allow-listed detail model; media, vectors and full trace IDs are excluded. */
export function buildResultDetails(capabilityId, result) {
  const capability = getCapability(capabilityId);
  if (!result || typeof result !== "object") {
    return Object.freeze([]);
  }
  const details = [];
  const add = (key, label, value) => {
    if (value === null || value === undefined || value === "") {
      return;
    }
    details.push(Object.freeze({ key, label, value: String(value) }));
  };

  const suggestionView = getSuggestionView(result);
  add("suggestion", "算法建议", suggestionView.label);
  const reasons = translateReasons(result.reason_codes);
  add("reasons", "原因", reasons.length ? reasons.join("、") : "无");
  const processingTime = finiteText(result.processing_time_ms, 1);
  add("processing-time", "处理耗时", processingTime === null ? null : `${processingTime} ms`);
  add("config-version", "配置版本", result.config_version);
  add("trace-id", "追踪标识", maskedIdentifier(result.trace_id));

  if (capability?.id === "portrait") {
    add("output-size", "输出尺寸", dimensionsText(result.output_width, result.output_height));
    add("output-format", "输出格式", typeof result.output_format === "string" ? result.output_format.toUpperCase() : null);
    add("template-version", "模板版本", result.template_version);
    add("source-size", "原图尺寸", dimensionsText(result.original_width, result.original_height));
  } else if (capability?.id === "witness") {
    add("source-size", "原图尺寸", dimensionsText(result.original_width, result.original_height));
    const trustedCardCount = Array.isArray(result.card_regions) ? result.card_regions.length : null;
    add(
      "protection-mode",
      "保护方式",
      trustedCardCount === null
        ? null
        : trustedCardCount > 0
          ? "可信证卡已识别，人物与证卡已保护"
          : "人物保护已完成，证卡识别非必需",
    );
    add("card-count", "可信证卡数", trustedCardCount);
    add("card-candidates", "证卡候选数", Number.isInteger(result.card_candidate_count) ? result.card_candidate_count : null);
    add("protected-area", "前景保护面积", percentText(result.protected_area_ratio));
  } else if (capability?.responseKind === "image-verification") {
    add("similarity-score", "相似度", finiteText(result.similarity_score, 4));
    add("decision-threshold", "决策阈值", finiteText(result.decision_threshold, 4) ?? (hasUncalibratedSemantics(result, normalizedReasonCodes(result.reason_codes)) ? "未标定" : null));
    add("feature-dimension", "特征维度", result.feature_dimension === undefined ? null : `${result.feature_dimension} 维`);
    add("calibration", "校准状态", calibrationText(result.calibration));
    add("reference", "参考照片", sideText(result.reference));
    add("probe", "待核验照片", sideText(result.probe));
  } else if (capability?.responseKind === "video-verification") {
    add("mode", "核验模式", result.mode === "full" ? "完整" : result.mode === "quick" ? "快速" : null);
    const duration = finiteText(result.duration_seconds, 1);
    add("duration", "视频时长", duration === null ? null : `${duration} 秒`);
    if (result.frames && typeof result.frames === "object") {
      const valid = Number(result.frames.valid_frames);
      const analyzed = Number(result.frames.analyzed_frames);
      add("frames", "有效帧", Number.isInteger(valid) && Number.isInteger(analyzed) ? `${valid} / ${analyzed}` : null);
    }
    add("aggregate-score", "聚合分数", finiteText(result.aggregate_score, 4));
    add("median-score", "中位分数", finiteText(result.median_score, 4));
    add("minimum-score", "最低分数", finiteText(result.minimum_score, 4));
    add("pass-ratio", "通过帧比例", percentText(result.pass_ratio));
    add("track-coverage", "主轨迹覆盖率", percentText(result.primary_track_coverage));
    add("secondary-ratio", "次轨迹比例", percentText(result.secondary_track_ratio));
    add("feature-dimension", "特征维度", result.feature_dimension === undefined ? null : `${result.feature_dimension} 维`);
    add("calibration", "校准状态", calibrationText(result.calibration));
    if (result.liveness_evaluated === false) {
      add("liveness", "活体检测", "未评估（不得视为活体通过）");
    }
  }

  return Object.freeze(details);
}

function decodeBase64(value) {
  const compact = value.replace(/\s+/g, "");
  if (!compact || compact.length % 4 === 1 || !/^[A-Za-z0-9+/]*={0,2}$/.test(compact)) {
    throw new TypeError("Invalid base64 data URL payload");
  }
  if (typeof globalThis.atob !== "function") {
    throw new TypeError("Base64 decoding is unavailable in this runtime");
  }
  let binary;
  try {
    binary = globalThis.atob(compact);
  } catch {
    throw new TypeError("Invalid base64 data URL payload");
  }
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

/** Converts a strict base64 or percent-encoded data URL without touching the DOM. */
export function dataUrlToBlob(dataUrl) {
  if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:")) {
    throw new TypeError("Expected a data URL");
  }
  const comma = dataUrl.indexOf(",");
  if (comma < 5) {
    throw new TypeError("Malformed data URL");
  }
  const metadata = dataUrl.slice(5, comma);
  const payload = dataUrl.slice(comma + 1);
  const segments = metadata.split(";");
  const mimeType = segments[0] || "text/plain";
  if (!/^[A-Za-z0-9!#$&^_.+-]+\/[A-Za-z0-9!#$&^_.+-]+$/.test(mimeType)) {
    throw new TypeError("Invalid data URL media type");
  }
  const isBase64 = segments.slice(1).some((item) => item.toLowerCase() === "base64");
  let bytes;
  if (isBase64) {
    bytes = decodeBase64(payload);
  } else {
    let decoded;
    try {
      decoded = decodeURIComponent(payload);
    } catch {
      throw new TypeError("Invalid percent-encoded data URL payload");
    }
    bytes = new TextEncoder().encode(decoded);
  }
  return new Blob([bytes], { type: mimeType.toLowerCase() });
}

const BLOCKED_KEYS = new Set([
  "embedding",
  "embeddings",
  "feature",
  "features",
  "alpha",
  "matte",
  "raw_image",
  "raw_frame",
  "token",
  "cookie",
  "authorization",
  "path",
]);

const SAFE_FEATURE_METADATA_KEYS = new Set(["feature_dimension", "feature_version"]);

function normalizedKey(key) {
  return String(key)
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
}

function isBlockedKey(key) {
  const normalized = normalizedKey(key);
  const segments = normalized.split("_");
  return (
    BLOCKED_KEYS.has(normalized) ||
    (!SAFE_FEATURE_METADATA_KEYS.has(normalized) && segments.some((segment) => segment === "feature" || segment === "features")) ||
    segments.some((segment) => segment === "embedding" || segment === "embeddings") ||
    normalized.endsWith("_vector") ||
    normalized.endsWith("_vectors") ||
    normalized.endsWith("_token") ||
    normalized.endsWith("_cookie") ||
    normalized.endsWith("_authorization") ||
    normalized.endsWith("_path") ||
    normalized.startsWith("raw_")
  );
}

function base64Summary(key, value) {
  const length = typeof value === "string"
    ? value.length
    : value && typeof value.length === "number"
      ? value.length
      : 0;
  return `[已省略 ${key}：${formatNumber(length)} 字符]`;
}

function safeJsonValue(value, options, seen, depth, key = "") {
  if (normalizedKey(key).endsWith("_base64")) {
    return base64Summary(key, value);
  }
  if (isBlockedKey(key)) {
    return "[已屏蔽敏感字段]";
  }
  if (value === null || typeof value === "boolean" || typeof value === "number") {
    return Number.isFinite(value) || typeof value !== "number" ? value : String(value);
  }
  if (typeof value === "bigint") {
    return `${value}n`;
  }
  if (typeof value === "string") {
    return value.length <= options.maxStringLength
      ? value
      : `${value.slice(0, options.maxStringLength)}…[另有${formatNumber(value.length - options.maxStringLength)}字符已省略]`;
  }
  if (typeof value === "undefined" || typeof value === "function" || typeof value === "symbol") {
    return null;
  }
  if (depth >= options.maxDepth) {
    return "[已达到安全展示深度]";
  }
  if (seen.has(value)) {
    return "[循环引用]";
  }
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? "Invalid Date" : value.toISOString();
  }
  if (typeof Blob !== "undefined" && value instanceof Blob) {
    return `[Blob ${value.type || "application/octet-stream"}，${formatBytes(value.size)}]`;
  }
  if (ArrayBuffer.isView(value)) {
    return `[${value.constructor.name}，${formatNumber(value.byteLength)} 字节]`;
  }
  if (value instanceof ArrayBuffer) {
    return `[ArrayBuffer，${formatNumber(value.byteLength)} 字节]`;
  }
  if (value instanceof Error) {
    return `[${value.name || "Error"}]`;
  }

  seen.add(value);
  try {
    if (Array.isArray(value)) {
      const limited = value.slice(0, options.maxArrayLength).map(
        (item) => safeJsonValue(item, options, seen, depth + 1),
      );
      if (value.length > options.maxArrayLength) {
        limited.push(`[另有${formatNumber(value.length - options.maxArrayLength)}项已省略]`);
      }
      return limited;
    }
    if (value instanceof Map) {
      return safeJsonValue(Object.fromEntries(value), options, seen, depth + 1);
    }
    if (value instanceof Set) {
      return safeJsonValue([...value], options, seen, depth + 1);
    }

    const output = {};
    const descriptors = Object.getOwnPropertyDescriptors(value);
    for (const property of Object.keys(descriptors).sort()) {
      const descriptor = descriptors[property];
      output[property] = "value" in descriptor
        ? safeJsonValue(descriptor.value, options, seen, depth + 1, property)
        : "[访问器已省略]";
    }
    return output;
  } finally {
    seen.delete(value);
  }
}

/**
 * Produces stable, escaped JSON for text-only diagnostics. Large media and
 * biometric/private fields are summarized or blocked recursively.
 */
export function safeJsonText(value, options = {}) {
  const requested = typeof options === "number" ? { space: options } : options ?? {};
  const settings = {
    space: Math.max(0, Math.min(8, Number(requested.space ?? 2) || 0)),
    maxDepth: Math.max(1, Math.min(20, Number(requested.maxDepth ?? 8) || 8)),
    maxArrayLength: Math.max(1, Math.min(1_000, Number(requested.maxArrayLength ?? 100) || 100)),
    maxStringLength: Math.max(32, Math.min(10_000, Number(requested.maxStringLength ?? 1_000) || 1_000)),
  };
  const safe = safeJsonValue(value, settings, new WeakSet(), 0);
  return JSON.stringify(safe, null, settings.space)
    .replaceAll("<", "\\u003c")
    .replaceAll(">", "\\u003e")
    .replaceAll("&", "\\u0026")
    .replaceAll("\u2028", "\\u2028")
    .replaceAll("\u2029", "\\u2029");
}
