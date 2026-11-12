# 人脸识别服务端 HTTP 接口说明（调用方更新用）

适用代码：分支 `yuanzq/feat/offline-face-engine`，合并 `yuanzq/feat/feature-search` 之后，算法服务版本 1.4.0。本文替代旧版《人脸识别检测算法后端接口文档》第 7 章，与旧文档的差异在第 1 节逐条列出。

**线上现状（37 服务器 18001 端口）**：运行 `face-algorithm:search-49eb6e7-20261012`，版本 1.3.0，6 个路由（含 `/api/search_feature`，只支持 v1）。它的代码与本仓库的 `49eb6e7` 逐文件一致；该提交此前不在任何分支上，现已找回并合并。对同一请求，合并后的服务与 37 的 v1 检索结果、v1 特征逐位一致。

服务分两层：

| 服务 | 默认端口 | 调用方 | 说明 |
|---|---|---|---|
| Python 算法服务（`FaceFeatureExtractorAPI`） | 8000，Compose 宿主只绑本机 18000 | **只给 Java 后端在内网调用** | 图片 → 人脸框 / 特征；特征 1:1 比对。无鉴权，不得暴露到公网 |
| Java 后端（`backend-java`） | 8080，Compose 宿主 18080 | 管理端浏览器、设备、业务系统 | 人员管理、1:N 检索、设备同步；管理接口需会话 + CSRF |

业务系统做人脸检索，应调用 **Java `POST /api/users/search`**，不要绕过 Java 直接调算法服务。

---

## 1. 与旧文档的差异（必读）

| # | 旧文档内容 | 实际代码 | 调用方要做什么 |
|---|---|---|---|
| 1 | 7.4 `POST /api/search_feature`（纯特征 1:N），一处写 1.3.0、一处写 1.4.0 | **存在**：1.3.0 起提供（37 线上即此版本）。1.4.0 起也接受 v2 特征 | v1 调用方式不变。用 v2 时查询和全部候选都声明 `arcface_r50_512`；响应的 `feature_dimension`/`feature_version` 回显版本。业务系统按照片查人仍优先用 Java `POST /api/users/search` |
| 2 | 7.3 `compare_feature` 版本固定 `recognizer_v1_1024`、恰好 1024 维 | 现在也接受 `arcface_r50_512`（512 维）；两侧版本必须相同 | v1 调用无需改动。用 v2 特征时两侧都声明 `arcface_r50_512` |
| 3 | 7.3 返回 `feature_dimension` 固定 1024、`feature_version` 固定 v1 | 回显本次请求的版本和维度（1024 或 512） | 不要把这两个字段写死成常量校验 |
| 4 | 7.1 "每侧扩展量为原宽或原高 × expand_scale / 2" | 按**人脸框**的宽高计算，不是整张图的宽高。实测框宽 89.3、`expand_scale=1` 时左边界外扩 44.7 | 按人脸框理解 |
| 5 | 7.1 "faces 为非空值数组" | 有脸时是数组；无脸时为空数组 `[]` 且 `success=false` | 以 `success` 判断 |
| 6 | 未列出 | 新增 `POST /api/v2/extract_feature`、`GET /api/feature_versions`；`GET /health`、`GET /ready` 一直存在但旧文档未写 | 见第 2、3 节 |
| 7 | 未说明 | 推理槽忙时**有界排队**（默认 32 个），不再立刻 429；可多进程部署 | 429 仍要有限退避重试，但频率会大幅下降 |
| 8 | 服务版本 1.2.0 / 1.3.0 | 完整服务为 **1.4.0**（含 v2 提取）；`Dockerfile.compare` 叠加在 1.1.0 镜像上为 1.3.0（无 v2 提取） | 判断能力以 `GET /api/feature_versions` 和 `/openapi.json` 的路由为准 |

---

## 2. 全部接口清单

### 2.1 Python 算法服务（共 8 个）

| 方法与路径 | 状态 | 用途 |
|---|---|---|
| `GET /health` | 未变 | 进程存活 |
| `GET /ready` | 未变 | 模型就绪 |
| `GET /api/feature_versions` | **新增** | 本进程已加载的特征版本 |
| `POST /api/detect_face` | 契约未变 | 人脸检测（只用 v1 模型） |
| `POST /api/extract_feature` | 契约未变，性能与排队行为变化 | v1 特征，1024 维 |
| `POST /api/v2/extract_feature` | **新增** | v2 特征，512 维，带质量原因 |
| `POST /api/compare_feature` | **有调整** | 1:1 特征比对，支持 v1 / v2 |
| `POST /api/search_feature` | **有调整**（1.3.0 起；1.4.0 支持 v2） | 纯特征 1:N，调用方提供 1–1000 个候选 |

另有 `GET /openapi.json`、`/docs`（FastAPI 自带）。`/api/v2/features/compare` 在代码里有定义但**未挂载**，访问返回 404。

### 2.2 Java 后端（与人脸识别相关的标注变化，其余未变）

| 方法与路径 | 鉴权 | 状态 |
|---|---|---|
| `POST /api/users/search` | 管理员 | **有调整**：可切换 v2、返回值不再含 `featureData`、新增错误码 |
| `POST /api/users/add` | 管理员 | **行为调整**：按配置同时提取多个特征版本 |
| `POST /api/users/update/{id}` | 管理员 | **行为调整**：同上；换照片时旧照片的 v2 特征作废 |
| `GET /api/features/status?version=` | 管理员 | **新增** |
| `POST /api/features/backfill?version=` | 管理员 | **新增** |
| `POST /api/features/backfill/stop` | 管理员 | **新增** |
| `GET /api/csrf`、`POST /api/login`、`GET /api/me`、`POST /api/logout` | — | 未变 |
| `GET /api/users/page`、`/list`、`/{id}`、`/count`；`DELETE /api/users/{id}`；`POST /api/users/deleteBatch` | 管理员 | 未变 |
| `GET /api/groups/page`、`/options`、`/count`、`/list`、`/{id}`；`POST /api/groups/add`、`/update`；`DELETE /api/groups/{id}` | 管理员 | 未变 |
| `POST /api/devices/register` | 设备 | 未变 |
| `GET /api/sync/users`；`/api/sync/v2/*`（state、bootstrap、page、finish、changes、ack）；`GET /ws/sync` | 设备 | 未变，仍只下发 v1 特征 |

未变接口的完整契约见 [API_DOCUMENTATION.md](../API_DOCUMENTATION.md)。

---

## 3. Python 算法服务接口详情

**通用约定**
- 图片接口都是 `multipart/form-data`，文件字段名 `image`。上传上限 10 MiB（`FACE_MAX_IMAGE_BYTES`），解码后上限 2500 万像素。
- 解码时按 EXIF 方向自动转正。
- 图片接口 HTTP 200 只代表请求处理完成，**必须再看 `success`**。
- 图片接口的错误格式：`{"detail": {"errorCode": "..."}}`。表单字段缺失时为 FastAPI 标准格式 `{"detail": [{"type": "missing", "loc": [...], ...}]}`，HTTP 422。
- 比对接口的错误格式不同：`{"success": false, "suggestion": "REVIEW", "reason_codes": [...], "message": "..."}`。

**图片接口错误码**

| HTTP | errorCode | 含义 |
|---|---|---|
| 400 | `INVALID_IMAGE` | 空文件或无法解码 |
| 400 | `INVALID_EXPAND_SCALE` | `expand_scale` 不在 0–10 或非有限数 |
| 404 | `FEATURE_VERSION_DISABLED` | 本进程未加载该版本（见 `FACE_FEATURE_VERSIONS`） |
| 413 | `IMAGE_TOO_LARGE` / `IMAGE_DIMENSIONS_TOO_LARGE` | 超过 10 MiB / 超过 2500 万像素 |
| 422 | —（FastAPI 标准 detail） | 缺少 `image` 字段 |
| 429 | `ALGORITHM_BUSY` | 排队已满，或排队等待超过超时 |
| 500 | `INFERENCE_FAILED` | 推理异常 |
| 502 | `INVALID_FEATURE` | 模型输出的特征无效 |
| 503 | `ALGORITHM_NOT_READY` / `ALGORITHM_UNAVAILABLE` | 模型未就绪或服务关闭中 |
| 504 | `ALGORITHM_TIMEOUT` | 超过 12 秒总期限（`FACE_INFERENCE_TIMEOUT_SECONDS`，含排队与读取上传） |

### 3.1 `GET /health`、`GET /ready`

```json
GET /health  → 200 {"status":"healthy","service":"Face Feature Extractor"}
GET /ready   → 200 {"status":"ready","busy":false,"restart_required":false}
             → 503 {"status":"not_ready",...}   初始化中、初始化失败，或单次推理超过 30 秒
```

`restart_required=true` 表示原生推理卡死，需要由部署方重启进程。负载均衡的健康检查用 `/ready`。

### 3.2 `GET /api/feature_versions`（新增）

```json
{"feature_versions": ["recognizer_v1_1024", "arcface_r50_512"]}
```

只返回本进程实际加载的版本。模型还在加载时返回空数组。

### 3.3 `POST /api/detect_face`（契约未变）

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `image` | 文件 | 是 | |
| `expand_scale` | number | 否 | 默认 0，范围 0–10 |

```json
{"success": true, "message": "Success",
 "faces": [{"x1": 82.6, "y1": 65.8, "x2": 171.9, "y2": 191.3, "score": 0.99998}],
 "processing_time": 0.031}
```

- 无脸时：`success=false`、`message="No face detected"`、`faces=[]`。
- `processing_time` 单位为秒。Windows 上计时精度约 15 ms，小于这个值会显示 0。
- **最多返回一张脸**（分数最高的那张，不一定是最大的），不能用来判断图中只有一个人。
- 不做旋转校正。
- 扩展量按人脸框宽高计算，结果裁剪到图片边界。
- 只在加载了 v1 时可用，否则返回 404 `FEATURE_VERSION_DISABLED`。

### 3.4 `POST /api/extract_feature`（v1，契约未变）

返回字段与旧文档 7.2 完全一致：`success`、`message`、`feature`（1024 个数）、`feature_dim`（1024）、`processing_time`（秒）。

```json
{"success": true, "message": "Success", "feature": [1024 个数], "feature_dim": 1024, "processing_time": 0.053}
{"success": false, "message": "No usable face detected", "feature": null, "feature_dim": null, "processing_time": 0.012}
```

**特征数值逐位不变**（150 张图前后比对一致）。已存特征和设备端无需任何处理。变化只有两点：更快（单张 P50 从 84 ms 降到 22 ms），以及忙时排队而不是立刻返回 429。

质量门控（全部通过才返回特征）：
- 亮度 70–230；
- 清晰度 ≥ 0.10；
- 姿态 |yaw| ≤ 30°、|pitch| ≤ 25°（FSANet）；
- 分辨率 112×112（小脸会先被放大，因此这一项基本总能通过）。

失败时只给出统一的 `message`，不说明具体原因。

### 3.5 `POST /api/v2/extract_feature`（新增）

只在 `FACE_FEATURE_VERSIONS` 包含 `arcface_r50_512` 时可用，否则返回 404 `FEATURE_VERSION_DISABLED`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `image` | 文件 | 是 | |
| `quality_filter` | bool | 否 | 默认 true；false 时质量不合格也返回特征（只建议在评测时用） |

```json
{
  "success": true,
  "reason_code": null,
  "feature_version": "arcface_r50_512",
  "feature": [512 个数，已 L2 归一化],
  "feature_dim": 512,
  "face": {
    "box": {"x1": 85.2, "y1": 68.7, "x2": 173.9, "y2": 192.6},
    "score": 0.80,
    "landmarks": [[x, y], [x, y], [x, y], [x, y], [x, y]],
    "pose": {"yaw": 8.7, "pitch": 0.1, "roll": 6.1},
    "quality": {
      "size":       {"value": 88.7, "passed": true},
      "pose":       {"yaw": 8.7, "pitch": 0.1, "roll": 6.1, "passed": true},
      "brightness": {"value": 161.1, "passed": true},
      "clarity":    {"value": 0.43, "passed": true},
      "cropped":    {"passed": true}
    }
  },
  "face_count": 1,
  "rotation": 0,
  "model_versions": {"detector": "det_500m.onnx", "recognizer": "w600k_r50_int8.onnx", "device": "cpu"},
  "processing_time_ms": 27.0
}
```

- **`processing_time_ms` 单位是毫秒**，v1 的 `processing_time` 单位是秒，字段名和单位都不同。
- **失败时仍是 HTTP 200**，`success=false`，`feature=null`，并给出 `reason_code`：

| reason_code | 含义 | `face` |
|---|---|---|
| `NO_FACE` | 四个方向都没检测到人脸 | null |
| `FACE_TOO_SMALL` | 人脸框短边 < 64 像素（`FACE_V2_MIN_FACE`） | 有 |
| `BAD_POSE` | \|yaw\| > 30° 或 \|pitch\| > 25°（5 点几何姿态，与设备端同一公式） | 有 |
| `TOO_DARK` / `TOO_BRIGHT` | 亮度 < 70 / > 230 | 有 |
| `TOO_BLURRY` | 清晰度 < 0.10 | 有 |
| `FEATURE_FAILED` | 特征计算失败 | 有 |

- 取画面中**面积最大**的人脸。`face_count` 是检测到的人脸总数，大于 1 不会拒绝。
- 正向检测不到时，依次尝试旋转 90°、270°、180°；`rotation` 是实际转过的角度，返回的坐标对应旋转后的图。
- 退化人脸算不出姿态时，`pose` 里的值为 `null`。
- `quality.cropped` 只做记录，不参与拒绝。

### 3.6 `POST /api/compare_feature`（有调整）

请求体 `application/json`，最大 128 KiB，总期限 5 秒，四个字段都必填：

| 字段 | 类型 | 约束 |
|---|---|---|
| `reference_feature` | number[] | 维度与版本一致：v1 恰好 1024，v2 恰好 512 |
| `probe_feature` | number[] | 同上 |
| `reference_version` | string | `recognizer_v1_1024` 或 `arcface_r50_512` |
| `probe_version` | string | **必须与 `reference_version` 相同** |

```json
{"success": true, "similarity_score": 0.8118, "feature_dimension": 512,
 "feature_version": "arcface_r50_512", "matcher_version": "l2_dot_v1", "processing_time_ms": 0.0}
```

- **校验规则沿用旧文档**：拒绝布尔值、字符串、NaN/Infinity、float32 溢出、零向量、多余或重复字段。
- **新增的拒绝情况**：两侧版本不同；维度与声明的版本不符（例如把 512 维向量声明成 v1）；未知版本。这些都返回 400 `INVALID_FEATURE`。
- **其他错误码**：413 `FEATURE_PAYLOAD_TOO_LARGE`、500 `INFERENCE_ERROR`、504 `ALGORITHM_TIMEOUT`。
- `similarity_score` 是余弦值，不是概率；接口不给出是否同一人的结论。
- **两个版本的分数不能互相比较，阈值也不能通用**，见第 6 节。
- 比对不占用推理槽，不受图片推理繁忙的影响。

### 3.7 `POST /api/search_feature`（1.3.0 起；1.4.0 支持 v2）

请求 `application/json`，**最大 32 MiB**，从收到请求起**绝对期限 5 秒**（含上传、解析、计算）。

| 字段 | 类型 | 约束 |
|---|---|---|
| `probe_feature` | number[] | 维度与版本一致：v1 1024，v2 512 |
| `probe_version` | string | `recognizer_v1_1024` 或 `arcface_r50_512` |
| `reference_version` | string | 全部候选的版本，**必须与 `probe_version` 相同** |
| `candidates` | object[] | 1–1000 个，每项只含 `id` 与 `feature`；`id` 为 1–128 字符、非纯空白、请求内唯一的字符串；`feature` 维度同上 |
| `top_k` | integer | 可选，默认 1，范围 1–候选数；只接受 JSON 整数 |

```json
{"success": true,
 "matches": [{"id": "Tony_Blair", "similarity_score": 0.586}, {"id": "Gerhard_Schroeder", "similarity_score": 0.016}],
 "candidate_count": 5, "top_k": 2, "feature_dimension": 512, "feature_version": "arcface_r50_512",
 "matcher_version": "l2_dot_v1", "processing_time_ms": 3.1}
```

- 算法与 1:1 相同（float32 → float64 归一化点积，截断到 [-1, 1]）；按分数降序，同分保持输入顺序，负分也可入选。不设阈值、不判定身份、不回传向量。
- 任一候选无效，整批返回 400；校验规则同 3.6（另含 ID 规则）。
- **每个服务进程只有一个 1:N 工作槽，不排队**，忙时立即返回 429 `FEATURE_SEARCH_BUSY`。工作槽在读取请求体之前占用，计算放在独立子进程里，不占图片推理槽。超时或断开后要等后台计算真正结束才释放，所以紧接着的请求仍可能收到 429。
- 错误：400 `INVALID_FEATURE`；413 `FEATURE_PAYLOAD_TOO_LARGE`；429 `FEATURE_SEARCH_BUSY`；500 `INFERENCE_ERROR`；503 `FEATURE_SEARCH_UNAVAILABLE`（1:N 子进程不可用，与图片模型无关）；504 `ALGORITHM_TIMEOUT`。错误格式同 3.6。
- 调用建议：复用连接、串行发送；每次都要带完整候选（服务端不缓存）。429 用有上限的退避加抖动重试。候选多、调用频繁时，改用 Java `/api/users/search`（服务端内存特征库，1 万人约 2 ms）。

---

## 4. Java 后端接口调整详情

Java 的统一错误格式不变：`{"code": HTTP状态, "errorCode": "...", "msg": "可展示文案"}`。

### 4.1 `POST /api/users/search`

请求不变：multipart，字段 `photo`（文件）和 `groupId`（正整数），都必填。

| 情况 | HTTP | errorCode |
|---|---|---|
| 命中 | 200 | `data` 为 User，**不再包含 `featureData`** |
| 比完全组无匹配 | 404 | `NO_MATCH` |
| 第一名与第二名过近（开启 `face.search.margin` 后才会出现） | 404 | `AMBIGUOUS_MATCH`（**新增**） |
| 照片里没有符合质量要求的人脸 | 422 | `NO_USABLE_FACE`；v2 时 `msg` 给出具体原因，如"人脸角度过大，请正对镜头" |
| 图片无效 | 422 / 413 | `INVALID_IMAGE` / `IMAGE_TOO_LARGE` |
| 检索期间人员数据变化 | 409 | `SEARCH_CHANGED`，重新检索即可 |
| 检索超时 | 503 | `SEARCH_INCOMPLETE`，表示没比完，**不能当作"查无此人"** |
| 算法服务不可用或繁忙 | 503 | `ALGORITHM_UNAVAILABLE` |
| 算法服务未启用检索所用版本 | 503 | `FEATURE_VERSION_DISABLED`（**新增**，属于部署配置错误） |
| 算法服务超时 | 504 | `ALGORITHM_TIMEOUT` |
| 排队超时 | 429 | `SEARCH_BUSY` / `ALGORITHM_BUSY` |
| 算法服务返回异常结果 | 502 | `ALGORITHM_INVALID_RESPONSE` |

行为变化：
- 检索版本由服务端配置 `FACE_SEARCH_FEATURE_VERSION` 决定，调用方无需传参，也无法指定。
- 现在从内存特征矩阵比对，1 万人约 2 ms。新人员入库后下一次检索就能查到。
- 并发默认 4，超出时在 5 秒预算内排队。

### 4.2 `POST /api/users/add`、`POST /api/users/update/{id}`

请求和响应格式都不变。行为变化：
- 按 `FACE_FEATURES_VERSIONS` 提取每个版本的特征，**任一版本提取失败，整个注册就失败**（返回 422 并带原因）。同时启用 v1 和 v2 时，注册比以前严格，耗时约为原来的两倍。
- 换照片时会删掉旧照片的 v2 特征。只改姓名或分组时不影响特征。
- 注意：这两个接口以及 `GET /api/users/{id}`、`/list` 的返回值里**仍然带 `featureData`**（v1 的 1024 维 JSON），见第 6 节建议。

### 4.3 特征补提（新增，管理员会话 + CSRF）

```
GET  /api/features/status?version=arcface_r50_512
→ {"code":200,"msg":"success","data":{
     "searchVersion":"recognizer_v1_1024",
     "backfill":{"version":"arcface_r50_512","running":false,"enrollable":3,"current":3,
                 "processed":3,"written":3,"failed":0,"recentFailures":[{"userId":12,"reason":"NO_USABLE_FACE"}]}}}

POST /api/features/backfill?version=arcface_r50_512   → 202 已启动；409 BACKFILL_RUNNING；version 为 v1 时 400
POST /api/features/backfill/stop                      → 200，当前这批处理完后停止
```

- `enrollable`：有照片的在职人数。`current`：已有当前照片对应 v2 特征的人数。
- 补提从库中存储的注册照提取特征，失败的人保持缺失；再次启动会重试这些人，相当于断点续跑。
- 补提期间有人重新注册，以新注册的结果为准。

---

## 5. 部署与配置

| 位置 | 变量 | 默认 | 说明 |
|---|---|---|---|
| 算法服务 | `FACE_FEATURE_VERSIONS` | `recognizer_v1_1024`（GPU 版默认两个都开） | 本进程加载的版本 |
| | `FACE_DEVICE` | `cpu` | `cpu` / `cuda` / `tensorrt`。GPU 不可用时启动直接报错，不会悄悄退回 CPU |
| | `FACE_WORKERS` | 镜像与 Compose 为 2，直接运行为 1 | 进程数，约每个物理核一个；每个进程约占 0.35 GB 内存（v2 额外加载模型，会更多） |
| | `FACE_ORT_THREADS` | CPU 镜像 1 / GPU 镜像 2，直接运行为 4 | 每个模型会话的线程数；多进程时设 1 |
| | `FACE_INFERENCE_QUEUE` | 32 | 每个进程的排队上限，0 表示不排队、直接拒绝 |
| | `FACE_V2_MODEL_DIR` | `./checkpoints/insightface` | v2 权重目录，运行时挂载，不入库，不进镜像 |
| Java 后端 | `FACE_FEATURES_VERSIONS` | `recognizer_v1_1024` | 注册时提取的版本 |
| | `FACE_SEARCH_FEATURE_VERSION` | `recognizer_v1_1024` | 检索使用的版本，必须包含在上一项里，否则后端拒绝启动 |
| | `face.search.threshold` / `face.search.v2-threshold` | 0.6 / 0.45 | 两个版本各自的检索阈值，**都未校准** |
| | `face.search.margin` | 0（关闭） | 第一名与第二名的最小分差 |
| | `face.extractor.max-concurrent` / `queue-wait-ms` | 2 / 3000 | 调用算法服务的并发数与排队等待时间；算法服务开多进程后应调大 |

镜像：
- **CPU 版**：`Dockerfile`，`docker compose up -d`。
- **GPU 版**：`Dockerfile.gpu`，`docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`。
- **现场叠加镜像**：`Dockerfile.compare` 在已部署的 1.1.0 旧镜像上叠加比对和检索接口，版本 1.3.0，6 个路由（与 37 现状相同），比对和检索支持 v1 / v2，**没有 v2 提取接口**。已在 1.1.0 代码上模拟过它的构建检查，可以通过。要用 v2 提取，需用 `Dockerfile` 或 `Dockerfile.gpu` 构建完整镜像。

启用 v2 的上线顺序：
1. 执行数据库迁移 `V20261016_01__user_feature.sql`。
2. 算法服务开启 v2 并挂载权重。
3. Java 后端设 `FACE_FEATURES_VERSIONS=recognizer_v1_1024,arcface_r50_512`，检索版本保持 v1。
4. 调用补提接口。
5. 状态接口显示 `current == enrollable`，并处理完补不上的人员后，切换检索版本到 v2，重启后端。
6. 回滚：把检索版本切回 v1 即可。

---

## 6. 注意事项

**调用方**
1. **始终检查 `success`**。图片接口在无脸或质量不合格时都返回 HTTP 200。
2. **两个版本的特征不能混用**：
   - 不能互相比对，后端会拒绝；
   - 阈值不能通用，两者分数分布差别很大（实测样例：v1 同一人 0.91；v2 同一人 0.81、陌生人 -0.01；LFW 上误识率 1e-5 对应的阈值 v1 为 0.70、v2 为 0.34）；
   - 存特征时必须同时记录版本号。
3. **字段名和单位不统一**：
   - 维度：提取接口叫 `feature_dim`，比对接口叫 `feature_dimension`；
   - 耗时：v1 提取和检测用 `processing_time`（秒），v2 提取和比对用 `processing_time_ms`（毫秒）。
4. **429 / 503 / 504**：有限次退避重试，间隔逐次加大并加随机抖动，不要立即无限重试。504 后服务端可能仍在计算，推理槽要等它算完才释放。
5. 检测接口最多返回一张脸；v2 提取取最大脸，不拒绝多人照片。需要"仅一人"的场景请自行判断 `face_count`。
6. `SEARCH_INCOMPLETE`（503）不能显示为"查无此人"。
7. **服务端没有活体检测**。检索和比对接口都不判断照片是真人还是翻拍，防伪要在采集端完成。

**部署方**
1. **InsightFace 权重仅限非商业研究用途**。v2 正式商用前必须确定授权路线：购买授权、自行训练，或采购商用 SDK。
2. **阈值未校准**：
   - v2 的 0.45 是保守默认值，LFW 上误识率 1e-5 约对应 0.34，1e-6 约对应 0.63。
   - 1:N 场景下，人员库越大，陌生人被误认成某人的概率越高。大分组应提高阈值，并开启 `face.search.margin`。
   - 上线前必须用自有数据校准。
3. **v2 质量门控和 v1 不同，存量补提可能有人补不上**：
   - 在 1000 张 LFW 图上，v1 拒绝 16%，v2 拒绝 4.1%，两边拒绝的原因不重合。
   - 两个版本都启用时，注册会更严格。
   - 切换检索版本前，必须处理 `recentFailures` 中补不上的人员（例如重新采集照片），否则这些人切换后检索不到。
4. 算法服务开多进程后，Java 的 `face.extractor.max-concurrent` 要同步调大，否则 Java 端会先成为瓶颈。
5. **管理接口泄露特征**：`users/add`、`users/update`、`users/{id}`、`users/list` 返回值里仍带 `featureData`，前端没有用到。建议下一版去掉（对外是接口变更，需要通知调用方）。
6. **直接改库不会被内存特征库感知**：绕过 Java 直接改 `sys_user` 或 `sys_user_feature`，已缓存的分组看不到这些改动，改完后需要重启后端。

---

## 7. 优化点与实测数据（本机：8 核 CPU / RTX 5070，压测客户端与服务同机）

| 项 | 优化前 | 优化后 |
|---|---|---|
| v1 单张提取 P50 / P95 | 83.9 / 119 ms | 21.9 / 23.5 ms，特征逐位不变 |
| v1 吞吐 | 11.9 张/秒，8 并发时 400 个请求里 370 个返回 429 | 单进程 34 张/秒；Linux 8 进程 108 张/秒，零错误 |
| 识别率（LFW 全配对 TAR@FAR=1e-5） | v1 88.32% | v2 CPU 版 99.86% / GPU 版 99.87% |
| v2 吞吐 | — | CPU 8 进程 58 张/秒（int8）；GPU 4 进程 105 张/秒 |
| 1:N 检索 | 每次从 MySQL 读整组 JSON 再逐条解析 | 内存矩阵扫描：1 万人约 2 ms，10 万人约 25 ms（1024 维，v2 减半） |
| 繁忙处理 | 立即返回 429 | 在超时预算内有界排队 |

速度提升主要来自修复一个原有 bug：三个模型会话把参数写成了 `session_options=`，参数被静默忽略，每个会话都开着 16 个空转线程。再加上限制 OpenBLAS / OpenCV 的线程数，以及对齐计算的向量化。

**以下未验证**：Docker 镜像未实际构建；server37 未部署复测；TensorRT 路径未测；压测客户端与服务在同一台机器上，实际服务器上的数值需要重新压测。
