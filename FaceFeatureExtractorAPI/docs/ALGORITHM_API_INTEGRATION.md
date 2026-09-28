# 算法 API 接入说明

业务开发调试请优先使用
[TASK-2026-001 新算法 API 联调文档](TASK-2026-001-API-DEBUGGING.md)。本文侧重安全边界、
部署约束和生产接入责任。

版本状态（2026-09-28）：presence 已部署，算法提交 `1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b`。
局域网算法地址为 `http://<server-37>:18002`，测试台为 `http://<server-37>:18003`。
[发布验收记录](reports/2026-09-28-presence-release-validation.md) 包含版本、测试和回滚证据。

## 1. 边界与责任

本服务只做算法适配：校验媒体、调用模块化算法、返回结构化建议。它不访问业务库，
不保存客户媒体，不持有合同状态，不创建人工复核任务。

业务开发负责：

- 选择并固化本次会话的可信参考照片，防止客户端任意替换参考；
- 合同有效性、人员授权、幂等、签署状态和审计链；
- full/presence 视频任务的异步队列、重试、超时补偿和结果回写；
- 原始证据的加密存储、访问控制、保留期限及人工复核流程；
- 把算法的 `PASS/RECAPTURE/REVIEW` 转换为业务动作；presence 身份结果独立使用 `decision/found`。

`full` 和 `presence` 路由是同步算法调用，只应由业务异步 worker 调用；APP 不应维持最长 600 秒的
HTTP 长连接。算法建议不等于合同审批结果。

## 2. 健康检查

| 路由 | 含义 | 状态码 |
|---|---|---|
| `GET /health/live` | 进程事件循环可响应，不代表模型可用 | 200 |
| `GET /health/ready` | 必需模型完成哈希、契约及 Provider 检查；artifact 模式还完成签名校准绑定 | ready 时 200，否则 503 |
| `GET /health` | 兼容别名，语义同 ready | 200/503 |
| `GET /openapi.json` | 类型化请求/响应与错误契约；已部署 presence，请重新导入 API 2.1.0 契约 | 200 |

`degraded + ready=true` 表示核心人脸能力可用、某个可选能力（例如 MODNet）不可用；
调用不可用能力仍返回 503，不能只看 liveness 接流量。
presence 对应 `capabilities.video_presence`；quick/full 共用 `video_verification`。
六条 v2 路由对应五个 readiness 能力键，模型 ready 不等于身份阈值已标定。

## 3. v2 路由

所有媒体使用 `multipart/form-data`，仅接受上传内容，不接受服务器文件路径。

| 路由 | 字段 | 截止时间 | 说明 |
|---|---|---:|---|
| `POST /api/v2/portrait/generate` | `image` | 30 秒 | 生成白底头肩照，成功响应含派生图 `image_base64` |
| `POST /api/v2/witness/blur-background` | `image` | 30 秒 | 人物优先保持清晰、可信证卡可选增强、虚化背景，成功响应含 JPEG 派生图 `image_base64` |
| `POST /api/v2/verify/images` | `reference_image`, `probe_image` | 30 秒 | 工资卡见证照与入场照 1:1 |
| `POST /api/v2/verify/video/quick` | `reference_image`, `video` | 60 秒 | 抽样持续一致性检查，不是目标出现 |
| `POST /api/v2/verify/video/full` | `reference_image`, `video` | 600 秒 | 整段持续一致性检查，不是目标出现 |
| `POST /api/v2/verify/video/presence` | `reference_image`, `video` | 600 秒 | 目标出现候选扫描，首版未标定，只供人工复核 |

默认 `DefaultAlgorithmServices` 已绑定模块化 `WitnessBlurRuntime`，因此会注册见证照虚化
路由。该能力只有在配置启用且 readiness 已验证 MODNet 与核心人脸模型时才可接流量；
模型不可用时安全失败为 503。自定义 services 若没有实现 `blur_witness_background`，路由
不会注册并返回 404。响应只接受确切的 `WitnessBlurResult` 契约，未知或伪造结构统一
失败为 500，不开放通用序列化。

人证接口的 `success=true` 只表示人物保护与背景虚化成功，不保证识别到证卡。
`card_regions` 非空表示这些可信证卡也通过保护预算并保持清晰；为空是合法的
“人物优先”结果，不应显示为失败，也不得把 `CARD_*` 原因混入成功响应。

当前内置证卡检测器只是可选增强的预生产基线，尚未通过客户标注集的召回率与误拒率
验收，因此不能对“所有证卡必定清晰”作业务承诺；人物优先的背景虚化不以它为成功
门禁。16 MP 内存边界和替换检测器所需标注见
[witness-blur-deployment.md](witness-blur-deployment.md)。

示例：

```bash
curl -f -F "reference_image=@trusted.jpg;type=image/jpeg" \
  -F "probe_image=@witness.jpg;type=image/jpeg" \
  http://face-algorithm:8000/api/v2/verify/images
```

算法语义结果（包括 `RECAPTURE` 和 `REVIEW`）使用 HTTP 200。响应可含 1024 这个
`feature_dimension` 契约值，但所有 v2 路由禁止返回 1024 维数组、embedding、alpha、
原图或视频帧。

presence 同样包含 `success,suggestion,reason_codes,model_versions,feature_version,config_version,
processing_time_ms,trace_id`。新增 `mode=presence`、`decision`、可空 `found`、同时可空的
`best_score/best_timestamp_seconds`、最多五项数字证据、扫描完整度、采样/解码/有效脸/候选计数、
候选预算触顶、时长和时间戳来源。完整字段、合法未标定 JSON、curl 与 Python 调用见联调文档
第 8A/13.6 节；不返回任何原始帧、embedding、路径或内部人脸对象。

首版恒定 `calibration_status=UNCALIBRATED`、`decision=INCONCLUSIVE`、`found=null`，
所有算法结果包含 `CALIBRATION_REQUIRED/THRESHOLD_UNCALIBRATED`，不允许 PASS。
FOUND/NOT_FOUND_IN_SCAN 只是未来保留状态；高分、无脸、完整扫描都不能触发当前身份结论。
参考图采用明确主脸选择，贴边检测框仍须关键点等质量门禁；视频按 0.5 秒采样、最多 1200
采样帧、每帧最多 5 候选。视频人脸 64 像素下限是未标定可用性策略，不是身份阈值。
候选预算/扫描预算不足分别披露 `CANDIDATE_LIMIT_REACHED/VIDEO_SCAN_LIMIT_REACHED`。
`liveness_evaluated=false`：无活体或反翻拍保证，也不证明真人在场。

## 4. 兼容路由与安全

- `POST /api/detect_face` 仅在 `legacy.detect_face_enabled=true` 时注册（候选默认 true），保持旧字段：`image`、可选 `expand_scale`；该值必须有限且位于
  `[0, 2]`，接口不返回特征。
- `POST /api/extract_feature` 默认未注册，因此是 404。

确需临时启用特征兼容接口时，同时满足：

1. 独立配置把 `legacy.extract_feature_enabled` 设为 true；
2. 用环境变量 `FACE_ALGO_LEGACY_TOKEN` 注入至少 32 UTF-8 字节的高熵令牌；
3. 调用方地址属于 `legacy.allowed_client_cidrs`；
4. 只有直接对端属于 `legacy.trusted_proxy_cidrs` 时才解析 `X-Forwarded-For`；
5. Uvicorn 必须使用 `--no-proxy-headers`，由本适配层独立验证完整代理链；
6. 请求头使用 `X-Internal-Token`，令牌经固定长度摘要后恒时比较。

任一条件失败统一返回 `403 + ACCESS_DENIED`。只有这条受信 legacy 路由可返回固定
1024 维数组；应限定迁移周期并由网关额外认证、限流和审计。不要把令牌写进 YAML、
镜像、日志或代码库。

## 5. 状态码与原因码

| HTTP | 典型原因码 | 处理 |
|---:|---|---|
| 200 | `NO_FACE`, `MULTIPLE_FACES`, `THRESHOLD_UNCALIBRATED`、预算限制原因 | presence 读取 decision/found，其余读取 suggestion |
| 400 | `INVALID_IMAGE`, `UNSUPPORTED_FORMAT`, `INVALID_VIDEO` | 修正上传 |
| 403 | `ACCESS_DENIED` | 检查 legacy 网关、令牌和代理链 |
| 413 | `IMAGE_TOO_LARGE`, `VIDEO_TOO_LARGE`, `VIDEO_TOO_LONG`, `VIDEO_SCAN_LIMIT_REACHED` | presence 输入解码预算超限或上传资源超限，按采集规格重传 |
| 503 | `MODEL_UNAVAILABLE`, `CALIBRATION_REQUIRED` | 停止流量并检查 readiness、模型卷或签名校准 |
| 504 | `ALGORITHM_TIMEOUT` | 包括等待唯一执行槽/解码超时，业务侧按幂等键补偿或转人工 |
| 500 | `INFERENCE_ERROR` | 记录调用方请求号、发生时间和接口；错误体不含 trace_id |

算法输出的 `success=false` 表示流水线未完成；`suggestion=REVIEW` 表示需要人工判断，
二者不能互相替代。
先检查 HTTP；错误体为 `{success:false,suggestion:"REVIEW",reason_codes:[...],message:...}`，
通常不含 trace_id。框架 404/405 或 multipart 解析失败可能只有 `detail`。缺少表单字段返回
400，而非 422。标准 v2 不要求内部令牌，但这不是公网匿名服务；网关仍必须提供身份与 TLS。
presence 的 `VideoInputError` 映射为 400（无效视频等）或 413（大小/时长/扫描输入预算），
deadline 映射 504。18002 忙时排队等到请求 deadline；不是立即返回 503，503 用于模型或
服务停止等不可用情况。18003 测试台另有并发槽即时 429，不能混用两者契约。

## 6. 阈值校准

交付配置保持 `calibrated: false`（当前 YAML 的等价状态是 `mode: uncalibrated`）。在没有
客户授权且按身份隔离的正负样本、ROC、FAR/FRR 和签名校准制品前，即使相似度很高，
图像与视频也必须返回 `REVIEW + THRESHOLD_UNCALIBRATED`，绝不能返回 PASS。
presence 独立于旧 quick/full 标定：即使旧视频发布有效校准，也不会自动开放 presence 身份
判定。首版 presence 只有 null 身份结果；将空值转换为 false 会制造错误的排除结论。

业务方提供的样本要按人员隔离训练/标定/测试集。算法方产出阈值和签名制品；业务方
负责发布审批、版本绑定、回滚以及把校准版本与合同审计记录关联。不可直接沿用历史
0.6/0.65/0.7/0.8 常量。

artifact 模式必须由业务组合工厂从独立信任源构造只读 `TrustedCalibrationKeyStore`，再用
`DefaultAlgorithmServices.from_config(..., calibration_key_store=trusted_store)` 显式注入。
公钥信任根不能塞回算法 YAML。默认 app factory 故意不猜测信任源：artifact 配置未注入
store、签名无效或模型/策略绑定不符时，启动阶段预建对应 image/video runtime 失败，
readiness 返回 503；请求侧统一为安全的 `CALIBRATION_REQUIRED`，不泄露路径或签名细节。

## 7. 上传、隐私与日志

- 单图默认最多 15 MiB、25 MP，见证照虚化本版经资源门禁后保留 16 MP；ASGI 入口从首个 body chunk
  起执行聚合大小和绝对
  deadline，handler 再限 UploadFile 字节、读取 JPEG/PNG header 校验尺寸，最后用忽略
  EXIF 自动旋转的 OpenCV 解码，统一交给现有 rotation classifier。
- 视频默认最多 500 MiB、600 秒、120 FPS；full/presence 最多分析 1,200 采样帧。MP4/AVI/MOV
  等按现有上传路径接收，解码仍取决于目标 OpenCV 编码能力，扩展名和 MIME 不代表已实测。
  上传流式写入
  权限受限的算法临时文件，阻塞写盘/flush/fsync 不占事件循环；成功、失败或超时都会
  关闭，超时后的文件由仍在运行的底层 worker 完成后删除。
- ASGI 层还限制整个 multipart body，防止未声明字段在 handler 前形成无限磁盘 spool。
- FastAPI multipart parser 会先形成一份 UploadFile spool，算法为路径式视频解码再形成
  一份私有临时文件；接近 500 MiB 上限时处理窗口会短时占用接近两份空间并有元数据
  开销。临时盘必须按此峰值和并发实测，不能配置成 512 MiB。
- 服务不记录或响应 embedding、alpha、原图、帧、证卡内容、令牌和文件路径；建议关闭
  Uvicorn access log，并在网关不记录 multipart body。
- 派生白底照/虚化图的像素数和编码字节均受输入资源上限约束，编码与 Base64 在唯一
  算法 owner/deadline 内完成；它们仅在响应中返回，持久化和加密属于业务侧。
- 默认不开放 CORS；确需浏览器调用时只通过 `FACE_ALGO_CORS_ORIGINS` 配置精确 origin，
  不允许 `*`，也不携带跨域凭据。

## 8. CPU 部署与容量

运行时固定 Python 3.12、`onnxruntime==1.28.0`、`torch==2.13.0+cpu`。镜像使用非 root
用户、单 worker、单线程算法执行门。一个超时的不可中断图像推理会继续在唯一 worker
中排空，但适配层不会提前释放所有权并并发进入同一 runtime；视频内部还使用可传递的
绝对 deadline。

```bash
docker build -t face-algorithm:cpu .
docker run --rm --read-only --tmpfs /app/tmp:rw,noexec,nosuid,size=2g \
  -p 8000:8000 \
  -v /secure/checkpoints:/app/checkpoints:ro \
  -v /secure/algorithm.yaml:/app/config/algorithm.yaml:ro \
  face-algorithm:cpu
```

`/app/tmp` 的 2 GiB 是单请求上限下的部署起点，不是通用容量承诺；tmpfs 还计入容器
内存。生产扩容以“一个容器一个 worker”为单位，不在同一进程增加 Uvicorn workers。
uncalibrated 能力按首次调用惰性持有各自隔离 runtime；artifact image/video 为保证真实
readiness 会在启动期预建。内存不能按单个 ONNX 文件简单相加。上线前必须在目标 CPU
上分别压测 quick/full/presence 的 P50/P95、峰值 RSS、临时盘和连续调用内存增长；当前工程不承诺
未知硬件上的 QPS。入口网关应限制并发和排队长度。

见证照背景虚化单能力在 16 MP 优化样本上的进程持久内存峰值约为 1.03 GiB；据此给出的
容器内存硬下限是 2.25 GiB，生产建议至少 3 GiB，并保留操作系统、解码和输入波动余量。
这不是组合部署的容量承诺：启用多种能力的组合服务必须在目标机器上重新实测峰值，
生产建议按能力拆分实例，并且每个实例始终只运行单 worker。

## 9. 上线清单

- 模型文件、大小、SHA-256、Provider 和 `recognizer_v1_1024` 契约校验通过；
- `/health/ready` 符合本次启用能力，未把 degraded 误当全部能力正常；
- 阈值仍未校准时，验收用例均不产生身份 PASS；
- full/presence 只从业务异步队列调用，可信参考和合同有效性在业务侧锁定；
- presence 高分和无可用脸都保持 INCONCLUSIVE/null，证据只有数字；不误用旧持续一致性规则；
- 网关关闭请求体日志，配置媒体大小、超时、TLS、认证和限流；
- legacy extract 若非必要保持 404；必要时完成令牌、CIDR、可信代理与下线日期审核；
- 客户数据不进入代码仓库、镜像、自动化测试 fixture 或普通运维日志。
