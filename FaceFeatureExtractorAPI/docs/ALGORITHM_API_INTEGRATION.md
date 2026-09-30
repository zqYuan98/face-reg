# 算法 API 接入说明

业务开发调试请优先使用
[TASK-2026-001 新算法 API 联调文档](TASK-2026-001-API-DEBUGGING.md)。本文侧重安全边界、
部署约束和生产接入责任。

2026-09-30 服务器 37 的 `18002/18003` 已正式发布 API `2.3.1`（代码 `4e6a337`），均 healthy。
portrait 已启用 `face_archive` 白底人脸照策略；
请求和响应字段不变，2.3.0 已引入两种原因枚举；成功有图可带 REVIEW 提醒。
发布状态、各项验收完成状态及回滚入口见 [检测框越界修正发布记录](reports/2026-09-30-portrait-box-overflow-release.md)。
直接升级基线与回滚目标为 `2.3.0 / 2557e17`：保留的原 API/测试台容器与原 presence `0.5` 配置；
本次配置字节哈希不变，不使用更早的 `d78 / 2.2.0` 作为本轮回滚目标。

更早历史（2026-09-28）：API `2.2.0`。
服务器 37 的 `http://<server-37>:18002` 与测试台 `http://<server-37>:18003` 在该次发布后均 healthy，
发布代码提交为 `d78e723b8fb8737b09e12859e57aa9856a640db6`，历史 `18001` 未改动。
该次部署证据与历史回滚记录见 [2.2.0 历史发布记录](reports/2026-09-28-presence-best-effort-release.md)。
37 在 2026-09-28 随后按用户指定仅将 presence 运行配置设为 `configured`、试用阈值 `0.5`，返回
`CONFIGURED_UNVALIDATED`，业务仍需人工 REVIEW；源码默认无阈值、代码版本、images/quick/full、
18001 和测试台在该次阈值调整中均不变。2.3.1 升级继续保留该阈值与 ID；配置依据见
[0.5 试用阈值记录](reports/2026-09-28-presence-threshold-050.md)。
功能上线或指定试用阈值不表示人工标签、生产阈值或识别精度已验收。

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
| `GET /openapi.json` | 类型化请求/响应与错误契约；本次已部署 2.3.1，接入须重新导入并核对 info.version | 200 |

`degraded + ready=true` 表示核心人脸能力可用、某个可选能力（例如 MODNet）不可用；
调用不可用能力仍返回 503，不能只看 liveness 接流量。
presence 对应 `capabilities.video_presence`；quick/full 共用 `video_verification`。
六条 v2 路由对应五个 readiness 能力键，模型 ready 不等于身份阈值已标定。

## 3. v2 路由

所有媒体使用 `multipart/form-data`，仅接受上传内容，不接受服务器文件路径。

| 路由 | 字段 | 截止时间 | 说明 |
|---|---|---:|---|
| `POST /api/v2/portrait/generate` | `image` | 30 秒 | 生成白底人脸照，不强制肩部；成功含 `image_base64`，有质量提醒时仍成功并为 REVIEW |
| `POST /api/v2/witness/blur-background` | `image` | 30 秒 | 人物优先保持清晰、可信证卡可选增强、虚化背景，成功响应含 JPEG 派生图 `image_base64` |
| `POST /api/v2/verify/images` | `reference_image`, `probe_image` | 30 秒 | 工资卡见证照与入场照 1:1 |
| `POST /api/v2/verify/video/quick` | `reference_image`, `video` | 60 秒 | 抽样持续一致性检查，不是目标出现 |
| `POST /api/v2/verify/video/full` | `reference_image`, `video` | 600 秒 | 整段持续一致性检查，不是目标出现 |
| `POST /api/v2/verify/video/presence` | `reference_image`, `video` | 600 秒 | 目标出现尽力扫描；可配置试用阈值三态，不是业务自动放行 |

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
`best_score/best_timestamp_seconds`、最多五项数字证据（各含 `candidate_index/quality_warnings`）、
`comparison_status`、扫描完整度、基础/补采/解码/有效脸/尝试及成功/失败候选计数、候选预算触顶、
请求/实际基础间隔、`sampling_strategy`、时长和时间戳来源，另有阈值/ID/策略指纹与软质量提示。
完整字段、三个合法 synthetic-only JSON、curl 与 Python 调用见联调文档第 8A/13.6 节；
不返回任何原始帧、embedding、路径或内部人脸对象。

沿用 2.2.0 presence 契约：服务端显式 `configured` 时返回 `CONFIGURED_UNVALIDATED`，完整成功扫描、
有比对且分数达到阈值可为 `FOUND/true`；全部低于阈值且没有候选截断或局部失败才为
`NOT_FOUND_IN_SCAN/false`。其余为 `INCONCLUSIVE/null`，尤其扫描不完整时即便已有高分也不能
给出肯定/否定结论。普通解码/推理失败的算法 DTO 可保留 partial 数字证据，并为
`comparison_status=FAILED`；硬截止/执行器超时仍返回 HTTP 504 `ApiErrorResponse`，不保证部分证据。
无可比脸不等于未出现；有限扫描未匹配也不证明全片绝对不存在。源码默认 `uncalibrated` 仍提取
1024 维特征并比对，返回 `THRESHOLD_NOT_CONFIGURED` 和证据，不要求签名校准或活体后才比较。
`suggestion` 保留 REVIEW（参考目标无效可 RECAPTURE），不允许业务 PASS。

参考照仍须目标明确；亮暗、模糊、姿态、小脸、贴边仅为软质量提示，参考 112px/视频 64px
不是硬拒绝门槛。技术上可对齐且特征有效的候选继续比较；候选级关键点/对齐/非有限或零特征
失败会记录并跳过，不中断其他候选；模型会话/维度/版本错误与未知异常仍为全局故障。
默认基础间隔 0.5 秒，每窗口最多补采 2 帧；无可比脸、质量提示、局部失败或候选截断会触发补采。
首尾、基础、补采去重后合计默认最多 1200 帧，长视频必要时增大实际基础间隔以覆盖全时域；
每帧最多 5 候选，不能只比最大脸，最多五条展示证据不等于只比较五个候选。
候选/扫描预算限制分别披露 `CANDIDATE_LIMIT_REACHED/VIDEO_SCAN_LIMIT_REACHED`，输入资源超限
仍按 400/413/504 映射，不因尽力扫描而取消安全限制。
`liveness_evaluated=false`：未执行且本接口不要求，不是失败门禁；无活体/反翻拍或真人在场保证。

## 4. 兼容路由与安全

2.3.0 portrait 保留原路由、multipart `image` 和全部响应字段，已引入
`PORTRAIT_SOURCE_LIMITED` / `PORTRAIT_UPSCALE_LIMITED` 两种提醒枚举。
业务根据 `success=true && image_base64` 判断出图，不能只接受 PASS；REVIEW 可表示已出图但有质量提醒。
`PASS` 仅表示图像处理完成，不是身份通过。归档由业务在考勤后触发，保存原始依据、选择目标、关联人员和幂等重试；
生成失败不推翻已有考勤。仓库交付配置显式设为 `face_archive`，不强制头顶/颈部/肩部，保留真实像素和有效脸部校验；
旧 YAML 缺 mode 则保持 `head_shoulders`，必须显式迁移。更多状态及示例见联调文档第 4 节。

2.3.1 补丁仅在 `face_archive` 质量路径允许有限的检测框外扩，不修改共享目标选择规则。
原框必须有限且宽高为正；左/右侧溢出各除以原框宽，上/下侧溢出各除以原框高，各值须不超过 5%，
裁至图内面积/原框面积须至少 90%。同时须选择到明确目标、五点形状/有限性/几何有效，
五点均在实际像素范围 `0 <= x < width, 0 <= y < height`，裁后宽高均至少 112px，后续抠图仍通过。
满足时使用图内框计算可见分辨率、质量和构图，成功有图为 `REVIEW` 并含 `PORTRAIT_SOURCE_LIMITED`，
可合并其他已有质量提醒；不是把 `FACE_CROPPED` 统一改成软码。
严重溢出、无效/越界关键点、裁后尺寸不足、推理异常或坏蒙版仍失败且无派生图；检测框外扩本身
不能证明真实脸部已缺失。只变换真实源像素，不补造缺失部位，portrait 不提取 embedding。
原 18 个响应字段及枚举、480×640 JPEG、30 秒预算、模板/模型/运行配置均不变；
严格 `head_shoulders` 和 witness/images/quick/full/presence 五个 v2 接口不变。
实际验收证据与已知限制以上方本次发布记录为准；不以接口规则本身替代实测证据。

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
| 200 | `NO_FACE`, `MULTIPLE_FACES`、旧接口 `THRESHOLD_UNCALIBRATED`、presence `THRESHOLD_NOT_CONFIGURED`、预算/局部失败原因 | presence 读取 decision/found；portrait 先看 success 与 image_base64，再看提醒；其余读取 suggestion |
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

## 6. 阈值校准与 presence 试用阈值（独立规则）

旧 images/quick/full 交付配置保持 `calibrated: false`（当前 YAML 的等价状态是 `mode: uncalibrated`）。在没有
客户授权且按身份隔离的正负样本、ROC、FAR/FRR 和签名校准制品前，即使相似度很高，
这些图像与视频接口也必须返回 `REVIEW + THRESHOLD_UNCALIBRATED`，绝不能返回 PASS。
以下签名制品和 artifact 信任源要求仅适用于旧图片/quick/full，不是 presence 的配置前提。

业务方提供的样本要按人员隔离训练/标定/测试集。算法方产出阈值和签名制品；业务方
负责发布审批、版本绑定、回滚以及把校准版本与合同审计记录关联。不可直接沿用历史
0.6/0.65/0.7/0.8 常量。

artifact 模式必须由业务组合工厂从独立信任源构造只读 `TrustedCalibrationKeyStore`，再用
`DefaultAlgorithmServices.from_config(..., calibration_key_store=trusted_store)` 显式注入。
公钥信任根不能塞回算法 YAML。默认 app factory 故意不猜测信任源：artifact 配置未注入
store、签名无效或模型/策略绑定不符时，启动阶段预建对应 image/video runtime 失败，
readiness 返回 503；请求侧统一为安全的 `CALIBRATION_REQUIRED`，不泄露路径或签名细节。

presence 使用独立服务端 `video_presence.mode`：源码默认 `uncalibrated` 的 `threshold/threshold_id`
必须为 null；`configured` 必须显式配置有限 `-1 < threshold < 1` 和非空 `threshold_id`，
HTTP 请求不接受调用方阈值。返回 `CONFIGURED_UNVALIDATED`、阈值/ID 和运行时生成的
`policy_fingerprint`，不能自称 ACTIVE 或生产精度已验证，也不新增签名证书系统。
默认工厂在启动时加载 `config/algorithm.yaml`（源码运行），可用 `FACE_ALGO_CONFIG` 指向
受控完整配置；变更后重启候选服务，不支持热更新。相对模型路径按该 YAML 所在目录解析。
没有推荐的默认身份阈值；标明 synthetic-only 的数值示例不能用于真实上线。
37 当前运行实例使用用户指定试用点 `0.5`，阈值 ID 为 `TASK-2026-001-presence-trial-050-20260928`，
不代表经过人工标签和风险目标验证的生产阈值。

视频最高分是 L2 归一化 1024 维特征点积，不是出现概率、真值或准确率。先按人员划分
tuning/evaluation，再由业务独立看完整视频标注 present/absent/uncertain/unknown；同名只代表
关联，不保证出现，不同组视频也不能自动当负例。试用点只能用 tuning 标签选择，evaluation
留出集不能反向调参；无标签只能报告覆盖率，生产精度仍未验证。格式、工具与泄漏检查见
[匿名出现标签与阈值验证](PRESENCE_LABEL_VALIDATION.md)。

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
- presence 验证 configured 三态与默认 uncalibrated 证据返回；无可比脸/扫描不完整仍为 INCONCLUSIVE/null，低于阈值但候选截断/局部失败不出 false；
- presence 证据只有数字和受限提示，质量软提示与补采不改变旧五接口、签名 calibration 或业务审核责任；
- 分清代码功能验收、人工标签阈值/精度验收和受控上线，未部署/未验证部分如实记录；
- 网关关闭请求体日志，配置媒体大小、超时、TLS、认证和限流；
- legacy extract 若非必要保持 404；必要时完成令牌、CIDR、可信代理与下线日期审核；
- 客户数据不进入代码仓库、镜像、自动化测试 fixture 或普通运维日志。
