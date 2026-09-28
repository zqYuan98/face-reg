# TASK-2026-001 新算法 API 联调文档

本文供业务后端、APP、测试和实施人员在公司内网联调人脸算法服务。
2026-09-28 已部署算法提交 `1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b`，新增独立 presence 接口。
完整测试、镜像、端口和回滚证据见 [发布验收记录](reports/2026-09-28-presence-release-validation.md)。

| 项目 | 值 |
|---|---|
| API 版本 | `2.1.0` |
| 配置版本 | `2026-08-24-v1` |
| 文档更新时间 | `2026-09-28`（presence 已部署，身份阈值仍未标定） |
| 特征契约 | `recognizer_v1_1024`，固定 `1024` 维 |
| 测试环境 Base URL | `http://<server-37>:18002` |
| 可视化测试台 | `http://<server-37>:18003` |
| OpenAPI JSON | `http://<server-37>:18002/openapi.json` |
| 网络范围 | 公司局域网；测试环境为 HTTP，不得暴露到公网 |

> 当前身份阈值仍处于 `UNCALIBRATED`。图片和视频即使相似度很高，也会安全返回
> `REVIEW + THRESHOLD_UNCALIBRATED`，不能将它解释为人员不一致，也不能自行写死
> `0.6`、`0.7` 等阈值。视频接口当前还会同时返回 `CALIBRATION_REQUIRED`。
> 新 presence 进一步固定 `decision=INCONCLUSIVE`、`found=null`，包括高分或无可用脸的情况。

## 1. 五分钟开始联调

### 1.1 检查服务

```bash
curl -sS http://<server-37>:18002/health/ready
```

满足以下条件才开始调接口：

- HTTP 状态码为 `200`；
- `ready=true`；
- 需要的能力为 `ready`：`portrait`、`witness_blur`、`image_verification` 或
  `video_verification` 或 `video_presence`；
- 当前测试环境应显示 `8` 个模型均为 `ready`。

就绪响应结构节选（线上已核验 8 个模型和 5 个能力键就绪）：

```json
{
  "status": "healthy",
  "ready": true,
  "models": [
    {
      "name": "face_recognizer",
      "state": "ready",
      "feature_version": "recognizer_v1_1024",
      "output_dimension": 1024
    }
  ],
  "capabilities": {
    "portrait": "ready",
    "image_verification": "ready",
    "video_verification": "ready",
    "video_presence": "ready",
    "witness_blur": "ready"
  }
}
```

| capability | 对应路由 |
|---|---|
| `portrait` | `/api/v2/portrait/generate` |
| `witness_blur` | `/api/v2/witness/blur-background` |
| `image_verification` | `/api/v2/verify/images` |
| `video_verification` | `/api/v2/verify/video/quick`、`/api/v2/verify/video/full` |
| `video_presence` | `/api/v2/verify/video/presence` |

`status=degraded + ready=true` 可能表示核心模型可用但某个可选能力不可用，因此必须检查
具体 capability，不能只判断顶层 `ready`。

### 1.2 导入 Postman/Apifox

可直接导入下面的 OpenAPI 地址：

```text
http://<server-37>:18002/openapi.json
```

服务关闭了 Swagger UI 和 ReDoc 页面，因此 `/docs`、`/redoc` 不可用是正常现象。
线上 OpenAPI 已同时描述 multipart 请求和类型化响应；请重新导入并核对六条 v2 路由。
应用字段校验的实际契约是 `400 + INVALID_IMAGE`，不是框架默认的 `422`。

Postman/Apifox 中所有文件字段都选择 `form-data` 的 **File** 类型，不要把文件路径、
Base64 或 JSON 当作请求体发送。

本文多行 `curl` 示例采用 Bash 的 `\` 续行符。Windows PowerShell 请使用 `curl.exe`
（避免 `curl` 别名），并改成单行或使用反引号续行。

### 1.3 当前测试环境鉴权与浏览器调用

- v2 测试接口当前不要求 Authorization 请求头，但仅允许在受控公司内网使用；
- 默认未开放 CORS，浏览器业务页面不要直接请求 `18002`；
- `18003` 只用于人工可视化调试，不是业务 API；正式业务应由业务后端调用 `18002`；
- `18001` 是旧版保留服务，TASK-2026-001 开发禁止接入；
- 当前链路是内网明文 HTTP，普通联调优先使用合成或已脱敏、已授权素材；
- 生产接入必须由业务网关补充 TLS、认证、限流和审计。

## 2. 接口总览

所有算法请求都使用 `multipart/form-data`。

| 能力 | 方法与路径 | multipart 字段 | 服务截止时间 |
|---|---|---|---:|
| 白底头肩照 | `POST /api/v2/portrait/generate` | `image` | 30 秒 |
| 人证背景虚化 | `POST /api/v2/witness/blur-background` | `image` | 30 秒 |
| 两图身份比对 | `POST /api/v2/verify/images` | `reference_image`, `probe_image` | 30 秒 |
| 视频快速持续一致性 | `POST /api/v2/verify/video/quick` | `reference_image`, `video` | 60 秒 |
| 视频完整持续一致性 | `POST /api/v2/verify/video/full` | `reference_image`, `video` | 600 秒 |
| 视频目标出现 | `POST /api/v2/verify/video/presence` | `reference_image`, `video` | 600 秒 |

其他 HTTP 接口也属于接入契约：

| 方法与路径 | 输入 | 响应/注册条件 |
|---|---|---|
| `GET /health/live` | 无 | 200，`{"status":"live"}`；仅进程存活 |
| `GET /health/ready` | 无 | 200/503，`status,ready,models,capabilities`；按所需能力判断 |
| `GET /health` | 无 | 与 ready 相同的兼容别名，不是仅检查存活 |
| `GET /openapi.json` | 无 | 200，OpenAPI 文档（`openapi,info,paths,components`） |
| `POST /api/detect_face` | `image`、可选 `expand_scale` | `legacy.detect_face_enabled=true` 时注册；候选默认 true |
| `POST /api/extract_feature` | `image` + legacy 鉴权 | 候选默认关闭/404；受控兼容场景见第 15 节 |

截止时间从请求进入算法服务开始计算，包含上传、multipart 解析、等待唯一算法执行槽、
推理和派生图编码。调试客户端超时应略大于服务截止时间，例如图片 40 秒、quick 75 秒、
full/presence 630～660 秒；不要通过大量并发请求绕过单执行槽。测试台代理视频完整/
presence 总预算为 620 秒，浏览器截止为 630 秒，服务算法预算仍为 600 秒。

## 3. 统一结果判定规则

### 3.1 HTTP 状态和算法建议是两层含义

HTTP `200` 表示服务已正常给出算法结果，不等于身份通过。业务代码必须优先读取
`suggestion`（presence 的身份结论必须改读 `decision/found`，见第 8A 节）：

| `suggestion` | 业务含义 | 推荐动作 |
|---|---|---|
| `PASS` | 当前能力成功；身份类接口还要求已发布有效标定包 | 进入下一业务步骤 |
| `RECAPTURE` | 拍摄质量或构图不符合要求 | 根据 `reason_codes` 提示重新拍摄 |
| `REVIEW` | 阈值未标定、身份不确定或需要人工处理 | 转人工/等待标定，不能自动放行 |

不要只判断 `success`：

- 白底照、人证虚化的 `success=true` 表示成功生成派生图；
- 身份类接口可能出现 `success=true + suggestion=REVIEW`，表示算法已完成，但当前不能
  自动作出身份结论；
- `success=false` 表示算法流水线未形成可用结果。

推荐判断顺序：

```text
1. 先判断 HTTP 状态码；
2. HTTP 200 时，presence 读取 decision/found；其他五个 v2 读取 suggestion；
3. RECAPTURE/REVIEW 时读取 reason_codes；
4. 未标定不得自动通过；presence 首版始终转人工，不把 success/suggestion 当身份结果；
5. 保存 trace_id、config_version、model_versions 和业务请求号用于审计。
```

### 3.2 公共响应约束

- `reason_codes` 始终是数组，成功时通常为空数组；
- `model_versions` 是本次实际参与计算的模型版本；
- `processing_time_ms` 仅为算法侧耗时，不包含完整业务链路时间；
- `trace_id` 由算法生成，可用于关联一次算法结果；
- `image_base64` 是原始 Base64 字符串，没有 `data:image/...;base64,` 前缀；
- v2 接口永远不返回 embedding、1024 维特征数组、原图、视频帧或原始抠图蒙版。

## 4. 白底头肩照

### 4.1 请求

```http
POST /api/v2/portrait/generate
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `image` | File | 是 | JPEG/PNG，最多 15 MiB、2500 万像素 |

```bash
curl -sS -X POST \
  -F "image=@./portrait.jpg;filename=upload.jpg;type=image/jpeg" \
  http://<server-37>:18002/api/v2/portrait/generate
```

### 4.2 成功响应

成功时 HTTP `200`，输出固定为 `480 × 640` JPEG。下面的 `model_versions` 仅节选两个
关键模型，实际响应会列出本次参与计算的全部模型：

```json
{
  "success": true,
  "suggestion": "PASS",
  "reason_codes": [],
  "model_versions": {
    "face_detector": "legacy-v1",
    "portrait_matting": "28165a451e4610c9d77cfdf925a94610bb2810fb"
  },
  "config_version": "2026-08-24-v1",
  "processing_time_ms": 1857.3,
  "trace_id": "a10486704a8c4684aa2accb61fce87a8",
  "template_version": "head-shoulders-480x640-v1",
  "output_width": 480,
  "output_height": 640,
  "output_format": "jpeg",
  "jpeg_quality": 95,
  "original_width": 1024,
  "original_height": 1536,
  "rotation_quarter_turns": 0,
  "placement": {
    "scale": 0.9807,
    "translate_x": -267.44,
    "translate_y": -45.05,
    "affine": [[0.9807, 0.0, -267.44], [0.0, 0.9807, -45.05]]
  },
  "matting_quality": {
    "face_mean": 0.9833,
    "face_coverage": 0.9838,
    "subject_area_ratio": 0.4619,
    "shoulder_depth_ratio": 0.6009,
    "shoulder_band_coverage": 0.9863
  },
  "image_base64": "<BASE64_JPEG_OMITTED>"
}
```

失败时仍可能是 HTTP `200`，但 `success=false`、`suggestion=RECAPTURE`，且不会返回
`image_base64`。业务侧根据 `NO_FACE`、`MULTIPLE_FACES`、`BAD_POSE`、
`SUBJECT_CROPPED` 等原因提示用户。

人物衣服、身体触及侧边或底边不再直接判定裁剪；真正的头顶或必要肩部被截断仍会
安全拒绝。

## 5. 人证背景虚化

### 5.1 请求

```http
POST /api/v2/witness/blur-background
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `image` | File | 是 | JPEG/PNG，最多 15 MiB、1600 万像素 |

```bash
curl -sS -X POST \
  -F "image=@./witness.jpg;filename=upload.jpg;type=image/jpeg" \
  http://<server-37>:18002/api/v2/witness/blur-background
```

### 5.2 成功响应

输出仍保持输入图片的宽高，人物主体保持清晰，背景被虚化。下面的
`model_versions` 为节选：

```json
{
  "success": true,
  "suggestion": "PASS",
  "reason_codes": [],
  "model_versions": {
    "face_detector": "legacy-v1",
    "portrait_matting": "28165a451e4610c9d77cfdf925a94610bb2810fb"
  },
  "config_version": "2026-08-24-v1",
  "processing_time_ms": 1789.5,
  "trace_id": "4151fb56c8e44e27827be82d921b4467",
  "original_width": 1024,
  "original_height": 1536,
  "rotation_quarter_turns": 0,
  "coordinate_space": "original_image",
  "card_regions": [
    {
      "corners": [
        {"x": 369.6, "y": 837.6},
        {"x": 601.2, "y": 837.6},
        {"x": 601.2, "y": 943.2},
        {"x": 369.6, "y": 943.2}
      ],
      "confidence": 0.8176
    }
  ],
  "card_candidate_count": 1,
  "protected_area_ratio": 0.4638,
  "matting_quality": {
    "face_mean": 0.9832,
    "face_coverage": 0.9838,
    "subject_area_ratio": 0.4619
  },
  "blur_summary": {
    "protected_area_ratio": 0.4638,
    "sigma_pixels": 25.6,
    "kernel_size": 151
  },
  "image_base64": "<BASE64_JPEG_OMITTED>"
}
```

重要语义：

- 人物是必须保护的主体，证卡只是可选增强；
- `card_regions=[]` 是合法成功结果，不能因此提示失败；
- `card_candidate_count` 是候选数，只有 `card_regions` 中的区域才是最终受保护的可信
  证卡区域；
- `coordinate_space=original_image`，证卡四角坐标对应原始输入图；
- 未检出人脸但分割得到可靠、唯一人物主体时，仍可能成功；
- 真实多人歧义、抠图可信度不足或保护区域接近全图时会拒绝出图；
- 输出 JPEG 会按 `92/88/84/80/76` 质量逐级尝试满足 15 MiB 上限。

## 6. 两图身份比对

### 6.1 请求

```http
POST /api/v2/verify/images
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `reference_image` | File | 是 | 由业务后端锁定的可信参考照；JPEG/PNG |
| `probe_image` | File | 是 | 本次采集的待核验照；JPEG/PNG |

每张图片最多 15 MiB、2500 万像素。参考照不能由客户端任意替换。

```bash
curl -sS -X POST \
  -F "reference_image=@./reference.jpg;filename=reference.jpg;type=image/jpeg" \
  -F "probe_image=@./probe.jpg;filename=probe.jpg;type=image/jpeg" \
  http://<server-37>:18002/api/v2/verify/images
```

### 6.2 当前未标定响应示例

下面是完整 DTO 结构示例（以既有合成样本结果为背景，质量/坐标等数值仅作说明，不是本次
上线实测证据）。相似度很高，但尚未发布客户标定包，结果仍为 `REVIEW`：

```json
{
  "success": true,
  "suggestion": "REVIEW",
  "reason_codes": ["THRESHOLD_UNCALIBRATED"],
  "model_versions": {"face_detector": "legacy-v1", "face_recognizer": "legacy-v1"},
  "feature_version": "recognizer_v1_1024",
  "config_version": "2026-08-24-v1",
  "processing_time_ms": 380.8,
  "trace_id": "3921fe20853447c7bf84ee8b4f538814",
  "reference": {
    "role": "reference",
    "analyzed": true,
    "usable": true,
    "reason_codes": [],
    "original_width": 1024,
    "original_height": 1536,
    "box": {"x1": 427.84, "y1": 190.53, "x2": 617.0, "y2": 503.78, "confidence": 0.99999},
    "landmarks": {"points": [{"x": 475.0, "y": 285.0}, {"x": 567.0, "y": 285.0}, {"x": 521.0, "y": 345.0}, {"x": 486.0, "y": 414.0}, {"x": 556.0, "y": 414.0}]},
    "quality": {"passed": true, "metrics": [], "reason_codes": []},
    "pose": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
    "rotation_quarter_turns": 0,
    "processing_time_ms": 181.0,
    "trace_id": "f6642f2333ea4abd81e9b77c8fb5d110"
  },
  "probe": {
    "role": "probe",
    "analyzed": true,
    "usable": true,
    "reason_codes": [],
    "original_width": 1024,
    "original_height": 1536,
    "box": {"x1": 427.84, "y1": 190.53, "x2": 617.0, "y2": 503.78, "confidence": 0.99999},
    "landmarks": {"points": [{"x": 475.0, "y": 285.0}, {"x": 567.0, "y": 285.0}, {"x": 521.0, "y": 345.0}, {"x": 486.0, "y": 414.0}, {"x": 556.0, "y": 414.0}]},
    "quality": {"passed": true, "metrics": [], "reason_codes": []},
    "pose": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
    "rotation_quarter_turns": 0,
    "processing_time_ms": 181.0,
    "trace_id": "probe-example-uncalibrated"
  },
  "similarity_score": 0.9999999999999998,
  "matcher_version": "l2_dot_v1",
  "feature_dimension": 1024,
  "decision_threshold": null,
  "calibration": {
    "status": "UNCALIBRATED",
    "calibration_id": null,
    "artifact_sha256": null
  }
}
```

实际 `quality.metrics` 会包含逐项质量指标，模型版本按实际运行返回。坐标基于
算法旋转校正后的图片，结合 `rotation_quarter_turns` 使用。

业务处理：

- 任一侧 `usable=false`：根据该侧 `reason_codes` 重新采集；
- `similarity_score=null`：本次没有形成可比较的双侧特征；
- `calibration.status=UNCALIBRATED`：不得根据 `similarity_score` 自行判断通过；
- 只有未来 `calibration.status=ACTIVE` 且 `suggestion=PASS` 时才能自动通过。

## 7. 视频快速核验

### 7.1 请求

```http
POST /api/v2/verify/video/quick
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `reference_image` | File | 是 | 可信参考照，JPEG/PNG |
| `video` | File | 是 | 推荐 MP4；最多 500 MiB、600 秒、120 FPS |

quick 默认抽取 12 帧，至少需要 5 个有效帧，适合持续身份一致性的交互式预检查。
它检查采样主体是否持续一致，不用于“某个人曾在视频某处出现”；该需求应使用 presence。

```bash
curl -sS -X POST \
  -F "reference_image=@./reference.jpg;filename=reference.jpg;type=image/jpeg" \
  -F "video=@./capture.mp4;filename=capture.mp4;type=video/mp4" \
  http://<server-37>:18002/api/v2/verify/video/quick
```

### 7.2 当前未标定响应示例

以下为完整结构示例；模型版本仅列示关键模型，完整字段类型见第 13 节：

```json
{
  "success": true,
  "suggestion": "REVIEW",
  "reason_codes": ["CALIBRATION_REQUIRED", "THRESHOLD_UNCALIBRATED"],
  "feature_version": "recognizer_v1_1024",
  "config_version": "2026-08-24-v1",
  "processing_time_ms": 4707.6,
  "model_versions": {"face_detector": "legacy-v1", "face_recognizer": "legacy-v1"},
  "trace_id": "e213eb2661df4f368b0c029d98e15784",
  "mode": "quick",
  "duration_seconds": 10.0,
  "frames": {
    "requested_frames": 12,
    "decoded_frames": 12,
    "analyzed_frames": 12,
    "valid_frames": 12,
    "no_face_frames": 0,
    "multiple_face_frames": 0,
    "bad_quality_frames": 0,
    "failed_frames": 0,
    "first_timestamp_seconds": 0.0,
    "last_timestamp_seconds": 9.75,
    "reason_codes": []
  },
  "aggregate_score": 0.98385,
  "median_score": 0.99152,
  "minimum_score": 0.98323,
  "pass_ratio": null,
  "primary_track_coverage": 1.0,
  "secondary_track_ratio": 0.0,
  "longest_no_face_gap_seconds": 0.0,
  "feature_dimension": 1024,
  "matcher_version": "l2_dot_v1",
  "calibration": {
    "status": "UNCALIBRATED",
    "calibration_id": null,
    "artifact_sha256": null,
    "frame_threshold": null,
    "aggregate_threshold": null,
    "min_pass_ratio": null,
    "decision_rule_version": null,
    "pipeline_fingerprint": "04a9df3be090e1a37df32289c9b293dedb05562e23ed1e54f3f7120819bea561",
    "policy_fingerprint": "346b8df8c1a20e62570ca4e5c8b229f82f07d8dfb6a1fb17499f97af75360ecd"
  },
  "liveness_evaluated": false
}
```

`liveness_evaluated=false` 表示当前接口没有活体检测能力，业务页面不能展示“活体通过”。

## 8. 视频完整核验

### 8.1 请求

```http
POST /api/v2/verify/video/full
Content-Type: multipart/form-data
```

字段与 quick 相同。full 默认每 0.5 秒抽一帧，至少需要 20 个有效帧，最多分析 1200 帧，
还会检查主轨迹覆盖率、第二主体比例、主体切换和最长无人脸间隔。这是持续一致性规则，
不等于视频内目标出现检索；新 presence 不改变这些旧门禁。

```bash
curl -sS -X POST \
  -F "reference_image=@./reference.jpg;filename=reference.jpg;type=image/jpeg" \
  -F "video=@./capture.mp4;filename=capture.mp4;type=video/mp4" \
  http://<server-37>:18002/api/v2/verify/video/full
```

响应结构与 quick 相同，`mode` 为 `full`。以下是完整结构示例；计数沿用既有 10 秒合成
视频背景，分数/耗时/标定摘要仅作说明，不是本次候选上线证据：

```json
{
  "success": true,
  "mode": "full",
  "duration_seconds": 10.0,
  "frames": {
    "requested_frames": 20,
    "decoded_frames": 20,
    "analyzed_frames": 20,
    "valid_frames": 20,
    "no_face_frames": 0,
    "multiple_face_frames": 0,
    "bad_quality_frames": 0,
    "failed_frames": 0,
    "first_timestamp_seconds": 0.0,
    "last_timestamp_seconds": 9.5,
    "reason_codes": []
  },
  "suggestion": "REVIEW",
  "reason_codes": ["CALIBRATION_REQUIRED", "THRESHOLD_UNCALIBRATED"],
  "model_versions": {"face_detector": "legacy-v1", "face_recognizer": "legacy-v1"},
  "feature_version": "recognizer_v1_1024",
  "config_version": "2026-08-24-v1",
  "processing_time_ms": 8000.0,
  "trace_id": "full-example-uncalibrated",
  "aggregate_score": 0.98,
  "median_score": 0.99,
  "minimum_score": 0.97,
  "pass_ratio": null,
  "primary_track_coverage": 1.0,
  "secondary_track_ratio": 0.0,
  "longest_no_face_gap_seconds": 0.0,
  "feature_dimension": 1024,
  "matcher_version": "l2_dot_v1",
  "calibration": {
    "status": "UNCALIBRATED",
    "calibration_id": null,
    "artifact_sha256": null,
    "frame_threshold": null,
    "aggregate_threshold": null,
    "min_pass_ratio": null,
    "decision_rule_version": null,
    "pipeline_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
    "policy_fingerprint": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "liveness_evaluated": false
}
```

full 是同步算法接口，但最长截止时间为 600 秒。APP/网页不要直接维持该长连接，应由
业务后端异步任务调用，并实现幂等、任务状态、结果回写和超时补偿。算法服务本身不保存
任务状态。

## 8A. 视频目标出现（presence，已部署）

### 8A.1 请求与边界

```http
POST /api/v2/verify/video/presence
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `reference_image` | File | 是 | 可信目标参考照，JPEG/PNG，≤15 MiB、25 MP |
| `video` | File | 是 | 待扫描视频，≤500 MiB、600 秒、120 FPS，单帧≤25 MP |

```bash
curl -sS --max-time 660 -X POST \
  -F "reference_image=@./reference.jpg;filename=reference.jpg;type=image/jpeg" \
  -F "video=@./capture.mp4;filename=capture.mp4;type=video/mp4" \
  http://<server-37>:18002/api/v2/verify/video/presence
```

参考图允许一个明确主脸，背景较小的脸不必直接拒绝；大小相近的多人歧义仍拒绝。
仅检测框贴边不直接失败，但关键点、姿态、模糊度、亮度和对齐质量仍须通过。
参考图的人脸最低尺寸策略为 112 像素，视频候选为 64 像素；这些是未标定的可用性策略，
不是身份阈值、不是识别准确率保证。多人视频逐候选评分，不把人数>1直接当作整帧失败。

默认按 0.5 秒间隔顺序扫描，最多 1200 个采样帧，每帧最多评估 5 个候选，保留最多 5 个
不同帧的最佳证据。其他人在场不等于目标不存在，也不要求目标全程持续出现。
短于采样间隔的出现、遮挡、模糊、过小人脸或候选预算触顶可能遗漏目标。
最大时长与采样预算同时生效：补采实际末帧也计入 1200 帧预算，接近 600 秒的视频可能
需要 1201 帧并返回 HTTP 413 / `VIDEO_SCAN_LIMIT_REACHED`；600 秒并非所有编码的可扫描保证。

### 8A.2 身份语义（必须保留 null）

| `decision` | `found` | 含义与本版可达性 |
|---|---|---|
| `INCONCLUSIVE` | `null` | 无法给出已标定身份结论；首版所有结果只能是这一组合 |
| `FOUND` | `true` | 保留状态，未来经独立标定后表示扫描中发现目标；本版不会产出 |
| `NOT_FOUND_IN_SCAN` | `false` | 保留状态，未来表示已扫描范围未找到；本版不会产出，也不是整段绝对不存在 |

`success=true` 只表示本次算法流程正常给出观察结果；`suggestion` 只控制重拍/人工处理，
不能替代 `decision/found`。首版固定 `calibration_status=UNCALIBRATED`，即便最佳分数接近 1、
没有可用脸或完整扫描完毕，也必须是 `INCONCLUSIVE + found=null`，不能转成 false 或 PASS。
无可评分候选时两个 `best_*` 同时为 null，`evidence_frames=[]`；这不是“没有此人”的证明。
参考图质量失败通常建议 RECAPTURE；系统处理失败 `success=false + REVIEW`。
所有算法 presence 结果均带 `CALIBRATION_REQUIRED`、`THRESHOLD_UNCALIBRATED`。

下面是完整、符合本版 DTO 的未标定结构示例，数值仅供说明，并非线上实测/识别率证据：

```json
{
  "success": true,
  "suggestion": "REVIEW",
  "reason_codes": ["CALIBRATION_REQUIRED", "THRESHOLD_UNCALIBRATED"],
  "model_versions": {"face_detector": "legacy-v1", "face_recognizer": "legacy-v1"},
  "feature_version": "recognizer_v1_1024",
  "config_version": "2026-08-24-v1",
  "processing_time_ms": 800.0,
  "trace_id": "presence-example-uncalibrated",
  "mode": "presence",
  "decision": "INCONCLUSIVE",
  "found": null,
  "best_score": 0.9234,
  "best_timestamp_seconds": 0.5,
  "evidence_frames": [
    {"frame_index": 15, "timestamp_seconds": 0.5, "score": 0.9234},
    {"frame_index": 0, "timestamp_seconds": 0.0, "score": 0.8123},
    {"frame_index": 29, "timestamp_seconds": 0.9666666667, "score": 0.7000}
  ],
  "scan_complete": true,
  "sampling_interval_seconds": 0.5,
  "sampled_frames": 3,
  "decoded_frames": 30,
  "valid_face_frames": 3,
  "candidate_count": 3,
  "candidate_limit_hit": false,
  "duration_seconds": 1.0,
  "timestamp_source": "pts",
  "calibration_status": "UNCALIBRATED",
  "feature_dimension": 1024,
  "matcher_version": "l2_dot_v1",
  "liveness_evaluated": false
}
```

`scan_complete=true` 仅表示采样扫描计划正常完成，不表示逐帧穷尽人脸或已完成身份标定。
`candidate_limit_hit=true`/`CANDIDATE_LIMIT_REACHED` 表示部分候选未评估；
`VIDEO_SCAN_LIMIT_REACHED` 表示扫描预算受限，应结合 `scan_complete` 与计数人工处理。
PTS 可用时优先采用；无法可靠获得时可能使用 FPS 推算，`timestamp_source` 会披露
`pts/estimated/mixed/unknown`。不要把 estimated 的小数位当作精确帧时间。

### 8A.3 Python 调用示例

安装 `requests` 后在业务 worker 中调用；先判断 HTTP，不能只看 success 或高分：

```python
import requests

url = "http://<server-37>:18002/api/v2/verify/video/presence"
with open("reference.jpg", "rb") as reference, open("capture.mp4", "rb") as video:
    response = requests.post(
        url,
        files={
            "reference_image": ("reference.jpg", reference, "image/jpeg"),
            "video": ("capture.mp4", video, "video/mp4"),
        },
        timeout=(5, 660),
    )
response.raise_for_status()  # 400/413/500/503/504 先按 HTTP 错误处理
result = response.json()
if result.get("calibration_status") == "UNCALIBRATED":
    assert result.get("decision") == "INCONCLUSIVE" and result.get("found") is None
    action = "manual_review"  # 首版只能复核；保留证据定位信息，禁止裸分数阈值
elif result.get("decision") == "FOUND" and result.get("found") is True:
    action = "review_version_and_calibration"  # 保留状态，本版不支持；不可直接自动放行
elif result.get("decision") == "NOT_FOUND_IN_SCAN" and result.get("found") is False:
    action = "review_scan_scope"  # 不声称整段视频绝对不存在目标
else:
    action = "manual_review"
print(action, result.get("decision"), result.get("best_timestamp_seconds"))
```

`requests` 的 read timeout 不是完整业务任务的绝对时限；队列仍需配置幂等、整体超时、
重试退避、结果回写和人工兜底。presence 与 full 一样是同步算法接口，不保存任务状态。
没有活体、反翻拍或“真人确实在场”保证，也不能替代签署全程持续一致性验证。

## 9. 媒体规格

| 项目 | 限制 |
|---|---|
| 通用图片格式 | 仅 JPEG、PNG；按文件真实头部识别，不只看扩展名/MIME |
| 通用单图大小 | 每张最多 15 MiB |
| 白底照/身份图片像素 | 每张最多 2500 万像素 |
| 人证虚化像素 | 最多 1600 万像素 |
| 视频大小 | 最多 500 MiB |
| 视频时长 | 最多 600 秒 |
| 视频帧率 | 最多 120 FPS |
| 单视频帧像素 | 最多 2500 万像素 |
| 视频格式 | 推荐 MP4；现有上传保留 `.mp4/.mov/.avi/.mkv/.webm/.m4v` 后缀，其他后缀匿名为 `.video`；实际容器/编码由 OpenCV 解码能力决定，不能只靠 MIME/扩展名保证支持 |

整个 multipart 请求体也有上限：单图接口约 `15 MiB + 64 KiB`，两图接口约
`30 MiB + 64 KiB`，视频接口约 `515 MiB + 64 KiB`。额外或未声明字段同样计入总请求体，
不能利用无关字段附带大文件。

JPEG 的 EXIF 自动旋转不会被 OpenCV直接采用，服务会使用自己的方向分类器统一校正。

MP4、AVI、MOV 可按原有上传路径提交，目标发布镜像仍须分别验证实际编码；现有合成回归
已验证 MP4V-in-MP4，不能据此声称所有 H.264/H.265/AVI/MOV 变体均已验收。视频会临时产生
multipart spool 和私有解码文件两份占用，按 500 MiB 上限需预留接近两份空间及余量。
服务单 worker/单算法槽；超时并不保证底层不可中断推理已立即停止，文件由持有者结束后清理。

## 10. HTTP 状态码与错误响应

### 10.1 标准错误结构

```json
{
  "success": false,
  "suggestion": "REVIEW",
  "reason_codes": ["IMAGE_TOO_LARGE"],
  "message": "image dimensions exceed the configured pixel limit"
}
```

| HTTP | 常见原因码 | 调用方处理 |
|---:|---|---|
| `200` | 业务语义结果 | presence 读取 `decision/found`；其余读取 `suggestion`，均检查 `reason_codes` |
| `403` | `ACCESS_DENIED` | 仅已启用的 legacy 特征路由鉴权失败；不要为联调打开权限 |
| `400` | `INVALID_IMAGE`, `UNSUPPORTED_FORMAT`, `INVALID_VIDEO` | 修正字段或媒体后重试 |
| `413` | `IMAGE_TOO_LARGE`, `VIDEO_TOO_LARGE`, `VIDEO_TOO_LONG`, `VIDEO_SCAN_LIMIT_REACHED` | 压缩/缩放、缩短或重新采集；presence 解码输入预算错误映射到此状态 |
| `404` | 路径不存在或可选能力未注册 | 核对版本、路径和 readiness |
| `405` | HTTP 方法不正确 | 使用本文规定的方法 |
| `500` | `INFERENCE_ERROR` | 记录时间和请求上下文，联系算法人员 |
| `503` | `MODEL_UNAVAILABLE`, `CALIBRATION_REQUIRED` | 暂停流量，检查 readiness/模型/标定包 |
| `504` | `ALGORITHM_TIMEOUT` | 包括等待唯一执行槽超时；不要立即并发重试，按幂等策略补偿 |

缺字段、字段名错误或 multipart 结构错误统一按 HTTP `400 + INVALID_IMAGE` 处理。
但请求甚至没有合法 multipart boundary、路径不存在或方法错误时，框架可能返回
`{"detail":"..."}`。客户端错误解析器应同时兼容标准 `reason_codes` 包和 `detail` 兜底，
不能假设所有非 2xx 都含 `trace_id`；当前标准 HTTP 错误包本身也没有 `trace_id`。

### 10.2 常见原因码

| 原因码 | 含义 | 推荐提示/处理 |
|---|---|---|
| `NO_FACE` | 未检测到有效人脸 | 请正对摄像头并确保脸部清晰可见 |
| `INVALID_IMAGE` / `UNSUPPORTED_FORMAT` | 图片损坏或不是 JPEG/PNG | 修正上传格式 |
| `IMAGE_TOO_LARGE` | 图片字节、像素或派生 JPEG 超限 | 等比例缩小或重新采集 |
| `MULTIPLE_FACES` | 多人或主次目标歧义 | 确保画面只保留本次办理人 |
| `FACE_TOO_SMALL` | 人脸占比过小 | 靠近摄像头重新拍摄 |
| `FACE_CROPPED` | 人脸被边界裁剪 | 保持完整脸部并留出边距 |
| `TOO_DARK` / `TOO_BRIGHT` | 光照不合格 | 调整环境光线 |
| `TOO_BLURRY` | 图像模糊 | 稳定设备、重新对焦 |
| `BAD_POSE` | 头部偏转过大 | 正视摄像头 |
| `LANDMARK_FAILED` / `ALIGNMENT_FAILED` | 关键点或对齐失败 | 重新拍摄；持续出现时联系算法人员 |
| `SUBJECT_CROPPED` | 白底照必要人物区域被裁剪 | 保留头顶和必要肩部 |
| `SHOULDERS_NOT_VISIBLE` | 肩部信息不足 | 调整为标准头肩构图 |
| `PORTRAIT_GEOMETRY_INVALID` | 无法放入标准证照模板 | 调整距离和构图 |
| `MATTING_LOW_CONFIDENCE` | 人物分割不可靠 | 换纯净背景、改善光线后重拍 |
| `MATTING_FAILED` | 抠图推理失败 | 转人工并联系算法运维 |
| `INVALID_VIDEO` / `VIDEO_DECODE_FAILED` | 视频为空、损坏或编码不受支持 | 使用已验证的 MP4 编码重新录制 |
| `VIDEO_TOO_LARGE` / `VIDEO_TOO_LONG` | 视频字节数或时长超限 | 压缩或缩短后重传 |
| `INSUFFICIENT_VALID_FRAMES` | 视频有效帧不足 | 延长稳定正脸时长后重拍 |
| `MULTIPLE_SUBJECTS` | 视频存在多个主体 | 只保留本次签署人 |
| `SUBJECT_SWITCH` / `SUBJECT_SWITCHED` | 视频主体发生切换 | 重新录制完整连续视频 |
| `THRESHOLD_UNCALIBRATED` | 身份阈值尚未标定 | 只能 REVIEW，不得自动判定 |
| `IDENTITY_UNCERTAIN` | 已标定但相似度未达通过条件 | 转人工复核 |
| `CALIBRATION_REQUIRED` | 缺少可信标定结果 | 检查标定发布状态 |
| `CANDIDATE_LIMIT_REACHED` | presence 单帧候选评估预算触顶 | 未评估候选可能含目标，不能推断不存在 |
| `VIDEO_SCAN_LIMIT_REACHED` | presence 扫描预算触顶 | 检查扫描完整度与计数，转人工 |
| `FEATURE_VERSION_MISMATCH` | 特征模型契约不一致 | 禁止比较，核对模型版本 |
| `FEATURE_FAILED` | 特征提取失败 | 转人工并联系算法人员 |
| `MODEL_UNAVAILABLE` | 所需模型不可用 | 检查 `/health/ready` |
| `ALGORITHM_TIMEOUT` | 超过本接口截止时间 | 按幂等策略稍后重试/转人工 |

人证虚化中的证卡识别是可选增强：`CARD_NOT_FOUND`、`CARD_TOO_SMALL`、
`CARD_CROPPED`、`CARD_DETECTION_UNCERTAIN` 不应成为人物虚化成功的业务门禁。

旧 quick/full 的媒体元数据或解码问题可能以 HTTP `200 + REVIEW` 返回。presence 对抛出的
解码输入错误作安全 HTTP 映射：视频大小/时长/扫描输入预算超限为 413，INVALID_VIDEO 等为
400，解码 deadline 超时为 504；不要把这些输入错误当成正常扫描。其他正常 200 结果仍须读取能力语义；presence 读取
`decision/found` 而不是把 REVIEW 理解为未出现。`18003` 代理另有 429（繁忙）、502/504
（上游异常/超时）等 `{success:false,error:{code,message}}` 错误包，不能当成 18002 算法 DTO。
18002 忙时会在唯一执行槽前等待直到本请求 deadline，队列超时为 504，不是立即返回 503；
503 用于模型或服务停止等不可用状态。不要把测试台的即时 429 与算法接口混为一谈。

## 11. 派生图片解码

Java：

```java
byte[] jpeg = java.util.Base64.getDecoder().decode(response.getImageBase64());
```

JavaScript/TypeScript：

```javascript
const binary = atob(result.image_base64);
const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
const blob = new Blob([bytes], { type: "image/jpeg" });
const previewUrl = URL.createObjectURL(blob);
// 预览销毁时执行 URL.revokeObjectURL(previewUrl)
```

不要把完整 `image_base64`、原图、视频、证卡内容或本地文件名写入普通业务日志。
15 MiB JPEG 转为 Base64 后接近 20 MiB，再加 JSON 开销；调用方网关和 HTTP 客户端的
响应体上限不能仍设为 15 MiB。

## 12. 联调检查清单

1. 调用 `/health/ready`，确认目标能力为 `ready`；
2. 使用 Postman/Apifox 或 `curl` 发 multipart 文件，不发 JSON/Base64 请求；
3. 上传时使用匿名文件名，例如 `upload.jpg`，不要透传身份证号、姓名等本地文件名；
4. 先验证白底照和人证虚化，确认能够解码 `image_base64`；
5. 图片/quick/full 按已标定的 `suggestion` 判定；presence 按 `decision/found`，不按 `success` 或裸分数；
6. 确认 `feature_dimension=1024`、`feature_version=recognizer_v1_1024`；
7. 当前未标定环境应得到 `REVIEW + THRESHOLD_UNCALIBRATED`，不得得到身份 `PASS`；
8. quick 可同步调试；full/presence 由业务异步 worker 调用；
9. 记录 `trace_id`、配置/模型版本和业务请求号，但不记录媒体及 Base64；
10. 出现 HTTP 500/503/504 时附上发生时间、接口、HTTP 状态和安全的结构化响应联系算法人员。

## 13. DTO 字段类型速查

记号：`T | null` 表示字段存在但允许为 `null`；`字段?` 表示该字段可能不出现在响应中。

### 13.1 公共字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `boolean` | 流水线是否正常给出结果；不能代替建议或 presence 身份结论 |
| `suggestion` | `"PASS" / "RECAPTURE" / "REVIEW"` | 处理建议；presence 首版禁止 PASS，身份只看 decision/found |
| `reason_codes` | `string[]` | 结构化原因；成功通常为空数组 |
| `model_versions` | `Record<string,string>` | 本次实际使用的模型版本 |
| `config_version` | `string` | 算法配置版本 |
| `processing_time_ms` | `number` | 算法侧非负耗时 |
| `trace_id` | `string` | 本次算法结果 ID；标准 HTTP 错误体没有此字段 |
| `feature_version` | `"recognizer_v1_1024"` | 身份图片、quick/full 和 presence 结果包含；派生图接口不包含 |

### 13.2 白底照字段

| 字段 | 类型 |
|---|---|
| `template_version` | `string` |
| `output_width`, `output_height`, `jpeg_quality` | `integer` |
| `output_format` | `"jpeg" / "png"`，当前部署为 `jpeg` |
| `original_width`, `original_height` | `integer` 或 `null` |
| `rotation_quarter_turns` | `integer`，范围 `0..3` |
| `placement` | `{scale,translate_x,translate_y:number, affine:number[2][3]}` 或 `null` |
| `matting_quality` | `{face_mean,face_coverage,subject_area_ratio,shoulder_depth_ratio,shoulder_band_coverage:number}` 或 `null` |
| `image_base64?` | `string`；仅成功生成派生图时存在 |

### 13.3 人证虚化字段

| 字段 | 类型 |
|---|---|
| `original_width`, `original_height` | `integer` 或 `null` |
| `rotation_quarter_turns` | `integer`，范围 `0..3` |
| `coordinate_space` | 固定 `"original_image"` |
| `card_regions` | `Array<{corners:Array<{x:number,y:number}>（固定 4 个点）,confidence:number}>` |
| `card_candidate_count` | `integer` |
| `protected_area_ratio` | `number`，范围 `[0,1]` |
| `matting_quality` | `{face_mean,face_coverage,subject_area_ratio:number}` 或 `null` |
| `blur_summary` | `{protected_area_ratio:number,sigma_pixels:number,kernel_size:integer}` 或 `null` |
| `image_base64?` | `string`；仅成功生成派生图时存在 |

### 13.4 两图比对字段

| 字段 | 类型 |
|---|---|
| `reference`, `probe` | `ImageVerificationSide` |
| `similarity_score` | `number` 或 `null`，范围 `[-1,1]` |
| `matcher_version` | 固定 `"l2_dot_v1"` |
| `feature_dimension` | 固定 `1024` |
| `decision_threshold` | `number` 或 `null`，未标定时为 `null` |
| `calibration` | `{status,calibration_id,artifact_sha256}` |

`ImageVerificationSide`：

| 字段 | 类型 |
|---|---|
| `role` | `"reference" / "probe"` |
| `analyzed`, `usable` | `boolean` |
| `reason_codes` | `string[]` |
| `original_width`, `original_height` | `integer` 或 `null` |
| `box` | `{x1,y1,x2,y2,confidence:number}` 或 `null` |
| `landmarks` | `{points:Array<{x:number,y:number}>（固定 5 个点）}` 或 `null` |
| `quality` | `{passed:boolean,metrics:Array<{name:string,value:number,passed:boolean}>,reason_codes:string[]}` 或 `null` |
| `pose` | `{yaw:number,pitch:number,roll:number}` 或 `null` |
| `rotation_quarter_turns` | `integer` 或 `null` |
| `processing_time_ms` | `number` 或 `null` |
| `trace_id` | `string` 或 `null` |

### 13.5 视频核验字段

| 字段 | 类型 |
|---|---|
| `mode` | `"quick" / "full"` |
| `duration_seconds` | `number` 或 `null` |
| `frames` | `FrameSummary` |
| `aggregate_score`, `median_score`, `minimum_score` | `number` 或 `null`，范围 `[-1,1]` |
| `pass_ratio` | `number` 或 `null`；未标定时为 `null` |
| `primary_track_coverage`, `secondary_track_ratio` | `number`，范围 `[0,1]` |
| `longest_no_face_gap_seconds` | `number` |
| `feature_dimension` | 固定 `1024` |
| `matcher_version` | 固定 `"l2_dot_v1"` |
| `calibration` | 视频标定摘要；未标定时阈值字段为 `null` |
| `liveness_evaluated` | 固定 `false` |

`FrameSummary` 包含：

```text
requested_frames, decoded_frames, analyzed_frames, valid_frames,
no_face_frames, multiple_face_frames, bad_quality_frames, failed_frames: integer
first_timestamp_seconds, last_timestamp_seconds: number | null
reason_codes: string[]
```

视频 `calibration` 包含：

```text
status: "UNCALIBRATED" | "ACTIVE"
calibration_id, artifact_sha256: string | null
frame_threshold, aggregate_threshold, min_pass_ratio: number | null
decision_rule_version: "video_track_aggregate_v1" | null
pipeline_fingerprint, policy_fingerprint: 64 位十六进制字符串
```

### 13.6 presence 完整附加字段

presence 包含第 13.1 节全部公共字段，额外字段如下；不复用 quick/full 的 `frames` 或
`calibration` 嵌套结构，不包含参考图、视频帧图片或任何特征数组。

| 字段 | 类型 / 含义 |
|---|---|
| `mode` | 固定 `"presence"` |
| `decision` | `"FOUND" / "NOT_FOUND_IN_SCAN" / "INCONCLUSIVE"`；本版仅 INCONCLUSIVE |
| `found` | `boolean | null`；本版仅 null，必须原样保留 |
| `best_score` | `number | null`，范围 [-1,1]，不是概率；与最佳时间同时可空 |
| `best_timestamp_seconds` | 非负 `number | null`，与 best_score 同时为空或非空 |
| `evidence_frames` | 0–5 个 `{frame_index:integer>=0,timestamp_seconds:number>=0,score:number[-1,1]}`；各帧序号唯一 |
| `scan_complete` | `boolean`，仅表示扫描计划完成，不等于逐帧穷尽或身份可信 |
| `sampling_interval_seconds` | 正 `number`，默认 0.5 |
| `sampled_frames` | 非负 `integer`，实际选取的采样帧 |
| `decoded_frames` | 非负 `integer`，顺序解码总帧数，含未采样帧 |
| `valid_face_frames` | 非负 `integer`，至少有一个可评分候选的采样帧 |
| `candidate_count` | 非负 `integer`，实际评估候选总数，不是视频里总人数 |
| `candidate_limit_hit` | `boolean`，是否曾超出每帧最多 5 候选的预算 |
| `duration_seconds` | 正 `number | null`，无法可靠获得时为 null |
| `timestamp_source` | `"pts" / "estimated" / "mixed" / "unknown"` |
| `calibration_status` | 本版固定 `"UNCALIBRATED"`，独立于 quick/full 校准包 |
| `feature_dimension` | 固定 `1024` |
| `matcher_version` | 固定 `"l2_dot_v1"` |
| `liveness_evaluated` | 固定 `false` |

计数应满足 `valid_face_frames <= sampled_frames <= decoded_frames`，
`valid_face_frames <= candidate_count <= sampled_frames * 5`。候选证据不泄露路径、帧像素、
embedding 或原始文件名；其排序/最佳值用于人工定位，不能充当未标定身份阈值。

## 14. 能力与业务模块对应

| 业务需求 | 调用接口 | 业务侧仍需负责 |
|---|---|---|
| 人脸照片自动生成 | `/api/v2/portrait/generate` | 保存派生照、归档权限和生命周期 |
| 证件拍摄背景虚化 | `/api/v2/witness/blur-background` | 业务展示、保存、人工复核 |
| 入场人员身份比对 | `/api/v2/verify/images` | 锁定可信参考照、人员/入场状态和审计 |
| 签署人脸一致性快速检查 | `/api/v2/verify/video/quick` | APP 采集交互和重拍提示 |
| 签署人脸一致性完整核验 | `/api/v2/verify/video/full` | 异步任务、合同状态、幂等、补偿和人工复核 |
| 视频中目标是否曾出现 | `/api/v2/verify/video/presence` | 锁定参考目标、异步扫描、证据复核；首版不作自动身份结论 |

算法服务不访问业务数据库，不判断合同是否有效，不创建人工任务，也不负责保存原始证据。

## 15. Legacy 兼容接口（按实际开关，不为联调开启权限）

候选 `config/algorithm.yaml` 当前为 `detect_face_enabled: true`、
`extract_feature_enabled: false`。发布环境仍应以配置和 OpenAPI 实际注册情况确认。

### 15.1 `POST /api/detect_face`

multipart：必填 `image`（JPEG/PNG，≤15 MiB、25 MP），可选 `expand_scale` 为有限数，
默认 0.0，范围 `[0,2]`。截止 30 秒；禁用检测开关时 404。

```bash
curl -sS -F "image=@./photo.jpg;filename=upload.jpg;type=image/jpeg" \
  -F "expand_scale=0.0" http://<server-37>:18002/api/detect_face
```

完整成功结构示例：

```json
{"success":true,"message":"Success","faces":[{"x1":100.0,"y1":50.0,"x2":200.0,"y2":150.0,"score":0.98}],"processing_time":0.02}
```

无脸是 HTTP 200：`{"success":false,"message":"No face detected","faces":[],"processing_time":0.02}`。
坐标裁切至解码图片边界；`processing_time` 单位是秒，与 v2 的毫秒字段不同。不返回特征。

### 15.2 `POST /api/extract_feature`

默认未注册，调用应为 HTTP 404；不要为调试 presence 开启此路由。仅在另行批准的迁移
部署同时启用开关、注入至少 32 UTF-8 字节高熵 `FACE_ALGO_LEGACY_TOKEN`、配置客户端 CIDR
与可信代理 CIDR 后可用。客户端必须带 `X-Internal-Token`；Uvicorn 必须禁用自动 proxy headers。
完整代理链仅在直接对端属于可信代理网段时解析，任一访问条件失败为 `403 + ACCESS_DENIED`。

已获授权时 multipart 仅必填 `image`（JPEG/PNG，≤15 MiB、25 MP），截止 30 秒。
成功响应字段为 `success:true,message:"Success",feature:number[1024],feature_dim:1024,
processing_time:number`，向量必须为有限 float32 固定 1024 维；此处不刊登或记录真实向量。
无可用脸的完整响应结构为：

```json
{"success":false,"message":"No usable face detected","feature":null,"feature_dim":null,"processing_time":0.02}
```

这是唯一允许返回特征的受控 legacy 例外。v2/测试台不提供该能力，也不转发内部令牌。
安全接入和回收期限见 [算法接入说明](ALGORITHM_API_INTEGRATION.md)。
