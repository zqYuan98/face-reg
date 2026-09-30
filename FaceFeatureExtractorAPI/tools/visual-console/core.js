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
const SAFE_UPLOAD_BASENAMES = Object.freeze({
  image: "visual-console-image",
  video: "visual-console-video",
});
const DEFAULT_UPLOAD_EXTENSIONS = Object.freeze({
  image: ".jpg",
  video: ".mp4",
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
    label: "白底人脸照",
    description: "生成考勤归档用白底人脸照，不强制完整肩部",
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
    description: "抽帧检查持续身份一致性，不是目标是否曾出现",
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
    description: "整段视频持续身份一致性，不是目标是否曾出现；正式业务应由异步 worker 调用",
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
  freezeCapability({
    id: "verify-video-presence",
    label: "视频目标出现",
    description: "按服务端试用阈值判断本次扫描是否匹配，提供分数、质量提示与候选证据",
    endpoint: "/api/v2/verify/video/presence",
    method: "POST",
    timeoutMs: 600_000,
    responseKind: "video-presence",
    fields: [imageField("reference_image", "目标参考照片"), videoField("video", "待扫描视频")],
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
  const extensionMatch = String(file?.name ?? "")
    .toLowerCase()
    .match(/\.[a-z0-9]{1,5}$/);
  const originalExtension = extensionMatch?.[0] ?? "";
  const extension = mimeExtension
    ?? (
      SAFE_KIND_EXTENSIONS[kind].has(originalExtension)
        ? originalExtension
        : DEFAULT_UPLOAD_EXTENSIONS[kind]
    );
  return `${SAFE_UPLOAD_BASENAMES[kind]}${extension === ".jpeg" ? ".jpg" : extension}`;
}

export function isCapabilityReady(capabilities, readinessKey) {
  return Boolean(
    capabilities
      && typeof capabilities === "object"
      && capabilities[readinessKey] === "ready",
  );
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
  ["presence", "verify-video-presence"],
  ["video_presence", "verify-video-presence"],
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
  THRESHOLD_NOT_CONFIGURED: "尚未配置判定阈值",
  MATTING_FAILED: "人像分割失败",
  MATTING_LOW_CONFIDENCE: "人像分割可信度不足",
  SUBJECT_CROPPED: "人物主体被裁切",
  SHOULDERS_NOT_VISIBLE: "肩部区域不可见",
  PORTRAIT_GEOMETRY_INVALID: "头肩照构图不符合要求",
  PORTRAIT_SOURCE_LIMITED: "源图范围受限，已保留可用人脸",
  PORTRAIT_UPSCALE_LIMITED: "放大倍率受限，已增加白色留边",
  CARD_NOT_FOUND: "未检测到证卡",
  CARD_TOO_SMALL: "证卡区域过小",
  CARD_CROPPED: "证卡被画面裁切",
  CARD_DETECTION_UNCERTAIN: "证卡检测结果不确定",
  INVALID_VIDEO: "视频无效",
  VIDEO_TOO_LARGE: "视频超过限制",
  VIDEO_TOO_LONG: "视频时长超过限制",
  VIDEO_DECODE_FAILED: "视频解码失败",
  CANDIDATE_LIMIT_REACHED: "候选人脸预算已达上限",
  VIDEO_SCAN_LIMIT_REACHED: "视频扫描预算已达上限",
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
    String(result?.calibration_status ?? "").toUpperCase() === "UNCALIBRATED" ||
    String(result?.calibration?.status ?? "").toUpperCase() === "UNCALIBRATED"
  );
}

/** Returns a fail-closed, presentation-ready interpretation of an API result. */
export function getSuggestionView(result, capabilityId) {
  if (result?.mode === "presence" || getCapability(capabilityId)?.responseKind === "video-presence") {
    return getPresenceView(result && typeof result === "object" ? result : {});
  }
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

  if (getCapability(capabilityId)?.id === "portrait" && result.success === true) {
    const warnings = normalizedReasonCodes(result.reason_codes);
    const allowed = new Set(["TOO_DARK", "TOO_BRIGHT", "TOO_BLURRY", "BAD_POSE",
      "PORTRAIT_SOURCE_LIMITED", "PORTRAIT_UPSCALE_LIMITED"]);
    const generated = Array.isArray(result.reason_codes)
      && typeof result.image_base64 === "string" && result.image_base64.length > 0
      && ((result.suggestion === "PASS" && warnings.length === 0)
        || (result.suggestion === "REVIEW" && warnings.length > 0 && warnings.every(code => allowed.has(code))));
    return Object.freeze({
      key: generated ? "generated" : "error", generated,
      label: !generated ? "结果不可用" : warnings.length ? "已生成，存在质量提醒" : "白底人脸照已生成",
      tone: !generated ? "danger" : warnings.length ? "warning" : "success",
      message: generated ? "已使用真实输入像素生成白底图片；此结果不代表身份核验或考勤通过。"
        : "出图状态与响应内容不一致，请检查后端响应。",
      notices: Object.freeze(translateReasons(warnings)), canTreatAsPass: false,
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

const PRESENCE_QUALITY_CODES = new Set(["TOO_DARK", "TOO_BRIGHT", "TOO_BLURRY", "BAD_POSE", "FACE_TOO_SMALL", "FACE_CROPPED"]);
const finiteNumber = (value) => typeof value === "number" && Number.isFinite(value);
const count = (value) => Number.isSafeInteger(value) && value >= 0;
const score = (value) => finiteNumber(value) && value >= -1 && value <= 1;
const nonemptyText = (value) => typeof value === "string" && value.trim().length > 0;
const reasonArray = (value) => Array.isArray(value) && value.every(nonemptyText);
const qualityArray = (value) => Array.isArray(value) && value.length <= 6
  && new Set(value).size === value.length && value.every((code) => PRESENCE_QUALITY_CODES.has(code));
const fingerprint = (value) => typeof value === "string" && /^[a-f\d]{64}$/.test(value);

function validPresenceObservations(result, modern) {
  if (result.mode !== "presence" || typeof result.success !== "boolean" || result.feature_dimension !== EXPECTED_FEATURE_DIMENSION
    || result.feature_version !== "recognizer_v1_1024" || result.matcher_version !== "l2_dot_v1"
    || result.liveness_evaluated !== false || !["REVIEW", "RECAPTURE"].includes(result.suggestion)
    || (!result.success && result.suggestion !== "REVIEW")
    || !reasonArray(result.reason_codes) || !result.model_versions || typeof result.model_versions !== "object"
    || Array.isArray(result.model_versions) || !nonemptyText(result.config_version) || !nonemptyText(result.trace_id)
    || !finiteNumber(result.processing_time_ms) || result.processing_time_ms < 0
    || typeof result.scan_complete !== "boolean" || typeof result.candidate_limit_hit !== "boolean"
    || !finiteNumber(result.sampling_interval_seconds) || result.sampling_interval_seconds <= 0
    || !["pts", "estimated", "mixed", "unknown"].includes(result.timestamp_source)
    || !(result.duration_seconds === null || (finiteNumber(result.duration_seconds) && result.duration_seconds > 0))) return false;
  if (!["sampled_frames", "decoded_frames", "valid_face_frames", "candidate_count"].every((field) => count(result[field]))) return false;
  if (result.valid_face_frames > result.sampled_frames || result.sampled_frames > result.decoded_frames
    || result.valid_face_frames > result.candidate_count || result.candidate_count > result.sampled_frames * 5
    || (result.scan_complete && (!result.success || result.sampled_frames === 0))
    || result.candidate_limit_hit !== result.reason_codes.includes("CANDIDATE_LIMIT_REACHED")) return false;
  const evidence = result.evidence_frames;
  if (!Array.isArray(evidence) || evidence.length > 5 || evidence.length > result.valid_face_frames) return false;
  if ((result.best_score === null) !== (result.best_timestamp_seconds === null)
    || (result.best_score !== null && (!score(result.best_score) || !finiteNumber(result.best_timestamp_seconds) || result.best_timestamp_seconds < 0))
    || Boolean(evidence.length) !== (result.best_score !== null)
    || Boolean(evidence.length) !== Boolean(result.valid_face_frames)) return false;
  if (new Set(evidence.map((frame) => frame?.frame_index)).size !== evidence.length) return false;
  for (const frame of evidence) {
    if (!frame || !count(frame.frame_index) || frame.frame_index >= result.decoded_frames || !score(frame.score)
      || !finiteNumber(frame.timestamp_seconds) || frame.timestamp_seconds < 0
      || (result.duration_seconds !== null && frame.timestamp_seconds > result.duration_seconds)) return false;
    if (modern && (!count(frame.candidate_index) || frame.candidate_index > 4 || !qualityArray(frame.quality_warnings)
      || !frame.quality_warnings.every((code) => result.quality_warnings.includes(code)))) return false;
  }
  const ordered = [...evidence].sort((a, b) => b.score - a.score
    || (modern ? a.quality_warnings.length - b.quality_warnings.length : 0)
    || a.timestamp_seconds - b.timestamp_seconds || a.frame_index - b.frame_index);
  if (evidence.length && (result.best_score !== ordered[0].score || result.best_timestamp_seconds !== ordered[0].timestamp_seconds)) return false;
  return !modern || ordered.every((frame, index) => frame === evidence[index]);
}

function validModernPresence(result) {
  if (!qualityArray(result.quality_warnings) || !validPresenceObservations(result, true)
    || !["COMPARED", "NO_COMPARABLE_FACE", "INVALID_REFERENCE", "FAILED"].includes(result.comparison_status)
    || result.sampling_strategy !== "presence_adaptive_v1"
    || !finiteNumber(result.requested_sampling_interval_seconds) || result.requested_sampling_interval_seconds <= 0
    || result.sampling_interval_seconds < result.requested_sampling_interval_seconds || !fingerprint(result.policy_fingerprint)
    || !["base_frames", "supplemental_frames", "compared_candidates", "failed_candidates"].every((field) => count(result[field]))
    || result.base_frames + result.supplemental_frames !== result.sampled_frames
    || result.compared_candidates + result.failed_candidates > result.candidate_count
    || (result.success && result.compared_candidates + result.failed_candidates !== result.candidate_count)
    || (result.suggestion === "RECAPTURE" && result.comparison_status !== "INVALID_REFERENCE")
    || result.valid_face_frames > result.compared_candidates
    || Boolean(result.valid_face_frames) !== Boolean(result.compared_candidates)) return false;
  if ((result.comparison_status === "FAILED") !== (result.success === false)
    || (result.comparison_status === "COMPARED") !== (result.success && result.compared_candidates > 0)
    || (result.comparison_status === "INVALID_REFERENCE" && (result.sampled_frames !== 0 || result.compared_candidates !== 0))) return false;
  const configured = result.calibration_status === "CONFIGURED_UNVALIDATED";
  if (configured) {
    if (!finiteNumber(result.threshold) || result.threshold <= -1 || result.threshold >= 1
      || !nonemptyText(result.threshold_id) || result.threshold_id.length > 128
      || result.reason_codes.includes("THRESHOLD_NOT_CONFIGURED")) return false;
  } else if (result.calibration_status !== "UNCALIBRATED" || result.threshold !== null || result.threshold_id !== null
    || !result.reason_codes.includes("THRESHOLD_NOT_CONFIGURED")) return false;
  if (result.decision === "INCONCLUSIVE") return result.found === null && (!configured || !result.success
    || !result.scan_complete || result.comparison_status !== "COMPARED"
    || (result.best_score < result.threshold && (result.candidate_limit_hit || result.failed_candidates > 0)));
  if (!configured || !result.success || !result.scan_complete || result.comparison_status !== "COMPARED") return false;
  if (result.decision === "FOUND") return result.found === true && result.best_score >= result.threshold;
  if (result.decision === "NOT_FOUND_IN_SCAN") return result.found === false && result.best_score < result.threshold
    && result.candidate_limit_hit === false && result.failed_candidates === 0;
  return false;
}

/** Validate the service decision, never infer one from a locally compared score. */
function getPresenceView(result) {
  const modern = Object.hasOwn(result, "comparison_status");
  const reasons = normalizedReasonCodes(result.reason_codes);
  const legacy = !modern && !["threshold", "threshold_id", "policy_fingerprint", "compared_candidates", "quality_warnings",
    "sampling_strategy", "base_frames", "supplemental_frames", "failed_candidates", "requested_sampling_interval_seconds"]
    .some((field) => Object.hasOwn(result, field));
  const validContract = modern ? validModernPresence(result)
    : legacy && validPresenceObservations(result, false) && result.success === true
      && result.calibration_status === "UNCALIBRATED" && result.decision === "INCONCLUSIVE" && result.found === null
      && reasons.includes("CALIBRATION_REQUIRED") && reasons.includes("THRESHOLD_UNCALIBRATED");
  const decision = validContract ? result.decision : "UNAVAILABLE";
  let label = "结果不可用，需人工复核";
  let message = "响应不符合 presence 契约，请检查 HTTP 状态和后端响应。";
  let key = "inconclusive";
  let tone = validContract ? "warning" : "danger";
  const notices = [];
  if (validContract) {
    message = "结果仅限本次实际扫描；抽样未匹配不代表整段视频从未出现目标。";
    if (decision === "FOUND") {
      label = "本次扫描检测到匹配";
      key = "found";
      tone = "success";
    } else if (decision === "NOT_FOUND_IN_SCAN") {
      label = "本次扫描未检测到匹配";
      key = "not-found";
    } else if (!modern) {
      label = "无法确定目标是否出现";
      message = "旧版未标定响应，仅展示历史候选证据，不作目标出现结论。";
    } else if (result.comparison_status === "FAILED") {
      label = "处理失败，无法完成判断";
    } else if (result.comparison_status === "INVALID_REFERENCE") {
      label = "参考目标不可用，无法比较";
    } else if (!result.scan_complete) {
      label = "扫描未完成，无法有效判断";
    } else if (result.comparison_status === "NO_COMPARABLE_FACE") {
      label = "未获得可比人脸，无法判断";
    } else if (result.calibration_status === "UNCALIBRATED") {
      label = "比对已完成，尚未配置判定阈值";
    } else {
      label = "证据不足，无法确定目标是否匹配";
    }
    notices.push(result.suggestion === "RECAPTURE" ? "业务建议：重新采集。" : "业务建议：人工复核（REVIEW），不自动放行。");
    if (result.calibration_status === "CONFIGURED_UNVALIDATED") notices.push("当前为试用阈值，识别精度尚未验证。");
  }
  return Object.freeze({ key, decision, validContract, legacy: !modern, label, message, tone,
    notices: Object.freeze(notices), canTreatAsPass: false });
}

const evidenceRenderTokens = new WeakMap();

/** Render bounded text and local seek actions; reset invalidates detached buttons too. */
export function renderEvidenceFrames(body, result, { onSeek } = {}) {
  if (!body) return;
  const token = {};
  evidenceRenderTokens.set(body, token);
  body.replaceChildren();
  const frames = Array.isArray(result?.evidence_frames) ? result.evidence_frames : [];
  const safeFrames = frames.filter((frame) => frame && Number.isInteger(frame.frame_index) && frame.frame_index >= 0
    && typeof frame.timestamp_seconds === "number" && Number.isFinite(frame.timestamp_seconds) && frame.timestamp_seconds >= 0
    && score(frame.score) && (!Object.hasOwn(frame, "candidate_index") || (count(frame.candidate_index) && frame.candidate_index <= 4))
    && (!Object.hasOwn(frame, "quality_warnings") || qualityArray(frame.quality_warnings))).slice(0, 5);
  for (const frame of safeFrames) {
    const row = body.ownerDocument.createElement("tr");
    const values = [String(frame.frame_index), frame.timestamp_seconds.toFixed(3), frame.score.toFixed(4),
      count(frame.candidate_index) ? String(frame.candidate_index) : "—",
      qualityArray(frame.quality_warnings) ? translateReasons(frame.quality_warnings).join("、") || "无" : "旧响应未提供"];
    for (const [index, value] of values.entries()) {
      const cell = body.ownerDocument.createElement("td");
      if (index === 1 && typeof onSeek === "function") {
        const button = body.ownerDocument.createElement("button");
        button.type = "button";
        button.className = "button button--quiet";
        button.textContent = `${value} 秒`;
        button.setAttribute("aria-label", `跳转本地视频至 ${value} 秒`);
        button.addEventListener("click", () => {
          if (evidenceRenderTokens.get(body) === token) onSeek(frame.timestamp_seconds);
        });
        cell.append(button);
      } else {
        cell.textContent = value;
      }
      row.append(cell);
    }
    body.append(row);
  }
}

/** Seeks only an existing browser-owned preview; never assigns or fetches a media URL. */
export function seekEvidencePreview(preview, localUrl, timestamp) {
  if (!preview || typeof localUrl !== "string" || !localUrl.startsWith("blob:") || preview.src !== localUrl
    || !finiteNumber(timestamp) || timestamp < 0 || !finiteNumber(preview.duration)
    || preview.duration <= 0 || timestamp > preview.duration) return false;
  try {
    preview.pause();
    preview.currentTime = timestamp;
    preview.focus();
    preview.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return true;
  } catch {
    return false;
  }
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

  const suggestionView = getSuggestionView(result, capabilityId);
  add("suggestion", capability?.responseKind === "video-presence" ? "身份结论" : "算法建议", suggestionView.label);
  const reasons = translateReasons(capability?.responseKind === "video-presence"
    ? normalizedReasonCodes(result.reason_codes).filter((code) => !["CALIBRATION_REQUIRED", "THRESHOLD_UNCALIBRATED"].includes(code))
    : result.reason_codes);
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
  } else if (capability?.responseKind === "video-presence") {
    const usable = suggestionView.validContract;
    add("decision", "本次扫描判定", usable ? result.decision : "不可用");
    add("found", "是否检测到匹配", usable ? result.found === true ? "是（true）" : result.found === false ? "否（false）" : "未确定（null）" : "不可用");
    add("business-suggestion", "业务建议", result.suggestion === "RECAPTURE" ? "重新采集" : "人工复核（REVIEW）");
    add("comparison-status", "比对状态", result.comparison_status);
    add("best-score", "最佳分数（不是身份概率）", finiteText(result.best_score, 4) ?? "无可评分候选");
    add("threshold", "本次使用的试用阈值", usable ? finiteText(result.threshold, 4) ?? "未配置" : "不可用");
    add("threshold-id", "试用阈值标识", usable ? result.threshold_id : null);
    const timestamp = finiteText(result.best_timestamp_seconds, 3);
    add("best-timestamp", "最佳候选时间", timestamp === null ? "无可评分候选" : `${timestamp} 秒`);
    add("scan-complete", "扫描完整", result.scan_complete === true ? "是（仅指采样计划）" : "否（扫描未完整覆盖）");
    const interval = finiteText(result.sampling_interval_seconds, 3);
    add("sampling-interval", "实际基础采样间隔", interval === null ? null : `${interval} 秒`);
    const requestedInterval = finiteText(result.requested_sampling_interval_seconds, 3);
    add("requested-sampling-interval", "请求基础采样间隔", requestedInterval === null ? null : `${requestedInterval} 秒`);
    add("sampling-strategy", "采样策略", result.sampling_strategy);
    for (const [field, key, label] of [["sampled_frames", "sampled-frames", "采样帧数"], ["decoded_frames", "decoded-frames", "解码帧数"],
      ["base_frames", "base-frames", "基础帧数"], ["supplemental_frames", "supplemental-frames", "补采帧数"],
      ["valid_face_frames", "valid-face-frames", "有可评分人脸的帧数"], ["candidate_count", "candidate-count", "尝试候选数"],
      ["compared_candidates", "compared-candidates", "成功比对候选数"], ["failed_candidates", "failed-candidates", "局部失败候选数"]]) {
      add(key, label, Number.isInteger(result[field]) ? result[field] : null);
    }
    add("candidate-limit", "候选预算触顶", result.candidate_limit_hit === true ? "是" : "否");
    add("timestamp-source", "时间戳来源", result.timestamp_source);
    const duration = finiteText(result.duration_seconds, 1);
    add("duration", "视频时长", duration === null ? "未知" : `${duration} 秒`);
    add("quality-warnings", "质量提示（不阻止比对）", qualityArray(result.quality_warnings)
      ? translateReasons(result.quality_warnings).join("、") || "无" : suggestionView.legacy ? "旧响应未提供" : "不可用");
    add("calibration", "阈值验证状态", !usable ? "不可用" : result.calibration_status === "CONFIGURED_UNVALIDATED"
      ? "试用阈值（精度未验证）" : "未配置判定阈值");
    add("policy-fingerprint", "策略指纹", usable ? result.policy_fingerprint : null);
    add("feature-dimension", "特征维度", result.feature_dimension === undefined ? null : `${result.feature_dimension} 维`);
    add("matcher-version", "匹配器", result.matcher_version);
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
const FEATURE_METADATA_SUFFIXES = new Set([
  "count",
  "dimension",
  "dimensions",
  "length",
  "size",
  "version",
]);
const SENSITIVE_FEATURE_VALUE_SEGMENTS = new Set([
  "array",
  "arrays",
  "data",
  "map",
  "maps",
  "payload",
  "tensor",
  "tensors",
  "value",
  "values",
  "vector",
  "vectors",
]);
const SENSITIVE_SUBJECT_SEGMENTS = new Set([
  "biometric",
  "debug",
  "face",
  "facial",
  "identity",
  "latent",
  "person",
  "representation",
  "subject",
]);
const SENSITIVE_VECTOR_CONTEXT_SEGMENTS = new Set([
  ...SENSITIVE_SUBJECT_SEGMENTS,
  "descriptor",
  "descriptors",
  "embedding",
  "embeddings",
  "feature",
  "features",
]);

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
  const hasFeatureSegment = segments.some(
    (segment) => segment === "feature" || segment === "features",
  );
  const hasEmbeddingSegment = segments.some(
    (segment) => segment === "embedding" || segment === "embeddings",
  );
  const hasVectorSegment = segments.some(
    (segment) => segment === "vector" || segment === "vectors",
  );
  const hasSensitiveVectorContext = segments.some(
    (segment) => SENSITIVE_VECTOR_CONTEXT_SEGMENTS.has(segment),
  );
  const hasSensitiveFeatureContext = segments.some(
    (segment) => SENSITIVE_SUBJECT_SEGMENTS.has(segment),
  );
  const hasSensitiveFeatureValue = segments.some(
    (segment) => SENSITIVE_FEATURE_VALUE_SEGMENTS.has(segment),
  );
  const isFeatureMetadata = SAFE_FEATURE_METADATA_KEYS.has(normalized) || (
    hasFeatureSegment &&
    FEATURE_METADATA_SUFFIXES.has(segments.at(-1)) &&
    !hasEmbeddingSegment &&
    !hasVectorSegment
  );
  return (
    BLOCKED_KEYS.has(normalized) ||
    hasEmbeddingSegment ||
    (
      !isFeatureMetadata &&
      hasFeatureSegment &&
      (hasSensitiveFeatureValue || hasSensitiveFeatureContext)
    ) ||
    (
      normalized === "vector" ||
      normalized === "vectors" ||
      (hasVectorSegment && hasSensitiveVectorContext)
    ) ||
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
