# TASK-2026-001 视频目标出现检测设计

## 批准依据与范围

2026-09-28 的 100 组现场数据复审已交付，用户随后明确要求“上线服务器，并且部署新接口，更新整个接口文档”。本设计落实已批准的独立出现检测方案，不改变旧 quick/full 的持续一致性契约。

备选方案已在复审中排除：直接降低旧阈值不能解决多人/覆盖率规则；原地改 quick/full 语义会破坏已联调调用方。采用新模块与新路由、旧接口保留的方式。

本次交付包含：独立算法、受控视频解码、HTTP 接入、安全序列化、测试台入口、完整接口文档与 OpenAPI、37 服务器 18002/18003 更新和回滚证据。18001、无关服务、SSH 授权、网络/防火墙均不改。

## 输入与输出

`POST /api/v2/verify/video/presence`，multipart 必填 `reference_image` 与 `video`；不接受调用方临时相似度阈值，也不让调用方指定服务器媒体路径。

公共 DTO 为独立 `VideoPresenceResult`，复用 `ResultBase` 的 success/suggestion/reason_codes/model_versions/feature_version/config_version/processing_time_ms/trace_id。

新增字段：

- `mode="presence"`，`decision`（FOUND、NOT_FOUND_IN_SCAN、INCONCLUSIVE）、`found`（bool 或 null）。
- `best_score`、`best_timestamp_seconds`、`evidence_frames`（有上限的最佳候选帧证据，含时间/分数，不含照片、特征、关键点或原始路径）。
- `scan_complete`、`sampling_interval_seconds`、`sampled_frames`、`decoded_frames`、`valid_face_frames`、`candidate_count`、`candidate_limit_hit`、`duration_seconds`、`timestamp_source`。
- `calibration_status="UNCALIBRATED"`、`feature_dimension=1024`、`matcher_version="l2_dot_v1"`、`liveness_evaluated=false`。

首版没有已标定的出现阈值。因此公共 DTO 和运行时必须一致禁止 FOUND/false-PASS：所有身份结论均为 INCONCLUSIVE、found=null；正常扫描 success=true/suggestion=REVIEW，并附 CALIBRATION_REQUIRED/THRESHOLD_UNCALIBRATED。FOUND/NOT_FOUND_IN_SCAN 是后续完成独立标定后才能激活的契约状态，不在首版伪造实现。无脸、低质量、参考照歧义、候选预算受限均有结构化原因。系统失败 success=false，不能被包装为未出现。

HTTP 200 仍只代表请求处理有结果；业务不能用 success 当 found。上传/超时/繁忙/系统错误复用现有 HTTP 错误规范。OpenAPI 应完整描述新 DTO，而非无类型 dict。

## 模块边界

新增 `face_algorithm/presence/` 包：

1. `config.py`：严格冻结的 `VideoPresencePolicy`，默认采样 0.5 秒、最大 1200 采样帧、每帧最多 5 候选、最多 5 帧证据。参考最小脸宽高 112，视频最小脸宽高 64；64 是保守的首版特征可用性筛选配置，不是身份匹配阈值，不可描述为已标定。保留原亮度/清晰度/姿态/关键点几何约束，视频质量策略与旧契约隔离。
2. `contracts.py`：公共无生物特征 DTO、内部观测 dataclass，禁止 vector/media 进入 HTTP。Result 模型验证不一致或伪造 PASS。
3. `analyzer.py`：依赖已校验模型构建的不可变 FacePipeline 组件；参考照使用现有 dominant_primary 选择器，歧义拒绝。视频对全部受限候选独立分析。检测框贴边不直接否决，但关键点必须在图内、几何可信、可见区域非空且达到质量要求。没有 monkeypatch，也不修改旧 FacePipeline 的策略。
4. `runtime.py`：组合参考提取、视频解码、逐候选匹配和证据聚合。所有模型版本一致，特征1024维且归一化；只在内存持有，结果不泄露。全片多人/换人/无脸间隔不作一票否决；证据按帧去重，返回最高分等定位数据，不以未标定 max 分数自动判定身份。
5. `__init__.py`：导出 `build_video_presence_runtime(config, registry)` 与公共 DTO。运行时方法 `verify(reference_image, video_path, *, deadline_monotonic)`。

配置集成：AlgorithmConfig 增加默认 `video_presence` 字段，旧配置不需要新增字段也能加载；显式 YAML 记录默认值。不得改变旧 calibration 指纹所覆盖的质量/视频字段。新配置拒绝 NaN、无效预算、额外阈值和未知字段，采样帧预算不得超共享资源上限。

## 独立受控解码

新增 `face_algorithm/video/presence_decoder.py`，复用原解码器的子进程生命周期、协议验证与资源约束；不改旧 quick/full 的采样语义。

接口为 `OpenCvPresenceDecoder.open(video_path, *, request, limits, deadline_monotonic)`，返回上下文迭代器。request 复用 `DecodeRequest(mode="full", full_interval_seconds=...)`；内部实现采用顺序读取、按实际媒体时间采样、首帧及真正最后解码帧补采，不随机 seek 容器宣称的末帧。

允许容器计数轻微误差；不能无条件把 read=false 当健康扫描。空流、严重/中途截断、非法尺寸/时间戳、超时/预算均 fail closed；应通过元数据与实际尾端差异的明确容忍界限和一致性检查区分正常 EOF，报告实际完成/时间来源。元数据是资源预检依据但不是精确末帧索引。PTS 缺失或不单调时使用明确标记的估算时间，不能伪称精确 PTS。

必须持续检查实际解码帧尺寸、时长、帧数与墙钟预算，不信任伪造 metadata。达到采样预算不得返回 scan_complete=true。消费者提前退出/超时/异常必须回收解码子进程与管道；上传临时文件沿用既有 executor 完成回调清理。

公开完成统计可通过流的 `summary` 提供：decoded_frames、sampled_frames、scan_complete、duration_seconds、timestamp_source。若现有协议需扩展，只新增兼容字段/新类型，不改变旧接口结果。

## API / 测试台 / 文档

DefaultAlgorithmServices 独立懒加载 presence runtime，ready 增加 video_presence 能力。接入沿用现有请求并发上限、CPU工作线程、上传限制与总请求deadline，presence 默认使用 video_full_seconds（600秒），不得使用 image 的30秒或quick的60秒。

测试台增加“视频目标出现”卡片，使用独立代理白名单、新响应校验与结果展示；保留原 quick/full 并明确其语义。展示未标定提示、最佳时间和证据，不把高分渲染成通过。复用既有视觉布局，不进行无关重设计。

更新开发联调总文档（所有现存接口、共同错误码/资源限制/鉴权边界、legacy兼容接口）、主 README、模块集成文档、测试台说明和可验证的 OpenAPI。生成不含媒体或密钥的请求示例，区分 API 与测试台地址。

## 验收与发布

- 单元测试：参考明确/歧义/贴边关键点、小脸、同框多人且目标非最大、短暂出现、低质量、全无脸、候选截断、未标定不判身份、1024校验与结果隐私。
- 解码测试：元数据差1/6帧、实际末帧、PTS fallback、损坏/空视频、严重截断、超预算/超时、取消与子进程清理。
- API测试：multipart字段、deadline/body limits、忙时503、临时文件清理、DTO校验、health/OpenAPI；旧回归保持。
- 测试台：白名单、未标定渲染、证据安全展示、视频大小/超时规则、旧功能不回归。
- 真实素材：100组新接口回归及匿名报告，重点验证96组诊断可比、3组无可用脸、1组参考歧义（数量如因生产64px策略有所变化必须解释，不把它当硬编码目标）。不得报告通过率或准确率。
- 发布：使用已有严格HostKey的yzq连接。先记录运行容器镜像/绑定/挂载/限制与回滚信息，构建固定提交镜像，候选验证后仅替换18002/18003；两端口保持仅绑定<server-37>。保留旧容器/镜像供回滚，验证18001未变。不得静默扩大公网暴露。
- 提交遵守公司规范 `feat|fix|docs|test(scope): TASK-2026-001 中文说明`，不提交.env、密钥、Token、模型/测试媒体、node_modules或构建产物。

## 不在本次范围

不训练新模型，不臆造阈值/标定证书，不对这100组自动贴身份真值标签；不提供活体/抗翻拍结论；不写业务合同状态或归档工作流；不清理用户其他文件和服务。
