# Video Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现独立视频目标出现检测接口及测试台、更新全部接口文档，验证后发布到37服务器18002/18003。

**Architecture:** 新presence包复用校验过的1024维模型与单请求执行槽，使用独立顺序子进程解码；旧quick/full不改。首版仅未标定证据输出，不伪造FOUND。新请求贯穿上传、deadline、清理、DTO与OpenAPI；部署固定提交并保留回滚。

**Tech Stack:** Python 3、NumPy、OpenCV、ONNX Runtime、Pydantic 2、FastAPI、原生JS测试台、pytest/node:test、Docker、严格HostKey SSH。

---

工作树：`C:/Users/24830/Documents/人脸识别/.worktrees/company-publish-prep/FaceFeatureExtractorAPI`。以下文件相对此目录。已在公司工作分支，继续该分支而不是创建main上的修改。

测试Python：`C:/Users/24830/Documents/人脸识别/.worktrees/face-algorithm-modular/face-reg-platform/FaceFeatureExtractorAPI/.venv/Scripts/python.exe`，下文用 `$py` 表示该路径。所有本地文本修改用apply_patch；禁止提交媒体、模型、密钥、.env、node_modules和构建产物。用户已批准上一轮书面复审并明确要求实现上线，不另起一个等待批准的任务。

## Task 1: 基线与发布边界

Files: 既有部署文档；本计划、设计文档；本地忽略的发布审计目录。

- [x] 阅读现有模块、100组实测与设计批准记录，独立审查设计。
- [x] 确认既有工作树清洁、位于公司TASK分支，保留主目录其他未提交内容。
- [ ] 运行 `& $py -m pytest tests/unit tests/api -q`，记录基线，不带故障进入新开发。
- [ ] 严格HostKey只读核对yzq服务器连接、运行容器/镜像、端口、挂载、资源及Docker构建入口；不打印密钥或Token。
- [ ] 实现计划独立复核，记录任务所有权，避免同文件并行编辑。

## Task 2: 模块化presence算法与公共DTO

Create: `face_algorithm/presence/__init__.py`, `config.py`, `contracts.py`, `analyzer.py`, `runtime.py`；`tests/unit/presence/test_config.py`, `test_contracts.py`, `test_analyzer.py`, `test_runtime.py`。

- [ ] 先写失败测试：新模块存在；策略拒绝非法预算/阈值；参考主脸歧义；bbox贴边但关键点完整；64px视频候选可进入质量评估；匹配目标不是最大脸；短暂出现不受全片覆盖率否决。
- [ ] 运行 `& $py -m pytest tests/unit/presence -q`，确认因新功能缺失失败。
- [ ] 新建严格冻结 `VideoPresencePolicy`（mode只允许uncalibrated，0.5秒、1200帧、5候选、5证据，参考112/视频64的独立尺寸策略）。不得修改旧质量配置。
- [ ] 新建 `VideoPresenceResult(ResultBase)` 与有界证据类型；实现等价以下不可绕过的边界：

```python
if result.calibration_status == "UNCALIBRATED":
    assert result.decision == "INCONCLUSIVE"
    assert result.found is None
    assert result.suggestion.value != "PASS"
```

- [ ] 建立候选分析器，复用rotation/detector/selector/landmarks/alignment/pose/quality/normalized1024流程；仅presence放开bbox贴边，保留关键点几何和图内边距。参考用现有dominant_primary，视频所有受限候选分别分析。不使用monkeypatch，不把特征放入public DTO。
- [ ] 实现 `build_video_presence_runtime(*, config, registry)` / `.verify(reference_image, video_path, *, deadline_monotonic)`，不可变模型快照、版本校验、跨候选最高分、按帧去重有界证据、扫描统计、错误转换和隐私边界。
- [ ] 解码依赖约定Task3的OpenCvPresenceDecoder；单元测试可注入Fake流，不依赖真实模型文件。
- [ ] 跑模块测试与 `tests/unit/core` 回归，修复失败；提交仅本任务文件，提交格式 `feat(video): TASK-2026-001 新增模块化视频目标出现检测`。
- [ ] 独立规格审查通过后，进行代码质量/安全审查，修复重要问题并复测。

## Task 3: 独立顺序视频解码器

Create: `face_algorithm/video/presence_decoder.py`, `tests/unit/video/test_presence_decoder.py`。

- [ ] 先写测试：旧容器总帧数多1/6时实际末帧仍补采；PTS优先和估算fallback；空流/严重截断失败；逐帧尺寸/时长/解码量预算；采样预算和deadline；提前退出子进程清理。
- [ ] 跑测试确认失败，然后复用既有decoder生命周期实现独立worker/stream。
- [ ] `open(video_path, request=DecodeRequest(mode="full", ...), limits=VideoResourceLimits(...), deadline_monotonic=...)` 返回上下文迭代器；每帧是现有DecodedFrame，stream.metadata是VideoMetadata，stream.summary提供完成统计。
- [ ] 明确允许尾端差异常量及测试，避免把任意read=false当健康EOF。采用实际decoded frame计数/时间和末帧，不随机seek末端；frame/pixel/fps/bytes/墙钟都有上限。summary.timestamp_source诚实标记pts或estimated/mixed。
- [ ] 计划采样全部处理且正常EOF才scan_complete=true；预算耗尽或解码异常不能伪装完整。所有退出路径回收进程与管道。
- [ ] 跑 `& $py -m pytest tests/unit/video -q`；针对上轮失败素材匿名做真实解码复验，不存媒体/原文件名。
- [ ] 独立规格审查→代码审查→修复；提交 `fix(video): TASK-2026-001 增加受控顺序出现检测解码器`。

## Task 4: API、配置与全接口OpenAPI

Modify: `face_algorithm/runtime/config.py`, `config/algorithm.yaml`, `face_algorithm/schemas.py`（仅必要原因码）；`api/services.py`, `api/routes/v2.py`, `api/serialization.py`, `api/body_limit.py`, `api/deadline.py`, `api/app.py`；新`api/openapi.py`用于公共响应说明；相关tests/api。

- [ ] 先写失败测试：presence路由与required multipart字段；响应$ref为新DTO；body limit映射、full预算、服务懒加载/关闭/readiness、结果隐私、超时清理与共用单执行槽。
- [ ] 将 `video_presence: VideoPresencePolicy=Field(default_factory=VideoPresencePolicy)` 加入AlgorithmConfig，验证不超过共享资源预算，旧yaml保持可加载。新增原因码CANDIDATE_LIMIT_REACHED、VIDEO_SCAN_LIMIT_REACHED。
- [ ] 独立服务方法调用runtime；新路由使用现有read_image_upload/spool_video_upload/transfer_cleanup/executor.run(on_complete=cleanup)，deadline从上传入口开始。
- [ ] presence默认复用video_full_seconds=600；不要改变quick60/full600/image30。独立路径必须加到body_limit和deadline白名单。
- [ ] 具体DTO序列化必须type精确检查、model_validate重新校验、只输出JSON安全字段；禁止把内部dataclass/ndarray转成公共dict。
- [ ] 新路由response_model明确；补齐旧公开v2与健康/错误响应的OpenAPI字段契约，保持实际响应兼容。legacy按现有开关与权限说明，不意外公开向量接口。
- [ ] 跑 `& $py -m pytest tests/api tests/unit/runtime -q`；验证旧路线/标定指纹兼容。
- [ ] 规格与代码审查后提交 `feat(api): TASK-2026-001 接入视频出现检测与类型化接口契约`。

## Task 5: 测试台与完整文档

Modify: `tools/visual-console/server.py`, `core.js`, `app.js`, `index.html`, `README.md`, 对应tests；`docs/TASK-2026-001-API-DEBUGGING.md`, `docs/ALGORITHM_API_INTEGRATION.md`, `docs/TASK-2026-001-VALIDATION.md`, API目录README、仓库README/API_DOCUMENTATION.md。

- [ ] 先写控制台代理白名单/readiness/预算/nullable decision显示测试，跑失败。
- [ ] 新增“视频目标出现”入口，仍只两份文件；旧quick/full名称明确“持续一致性”。展示最高分/时间/有界证据/未标定状态，不显示假通过，不解析服务端HTML。
- [ ] 更新所有接口文档总表、请求字段、完整响应、原因码/超时/资源/权限边界、curl/Python示例、新旧语义区分及版本记录。现存所有接口有索引，不仅追加新接口段落。
- [ ] 运行 `& $py -m pytest tools/visual-console/tests -q` 与 `node --test tools/visual-console/tests/core.test.mjs`；浏览器真实交互验证新入口和结果。
- [ ] 文档curl示例从实际响应/契约生成，不能伪造FOUND；OpenAPI导入可用性与路由覆盖核对。
- [ ] 规格与代码审查后提交 `feat(console): TASK-2026-001 支持视频目标出现联调` 和 `docs(api): TASK-2026-001 更新完整算法接口文档`。

## Task 6: 全回归与现场数据

Create: 隐私安全现场测试工具/匿名报告（忽略目录），仓库发布验收记录。

- [ ] 全跑 `& $py -m pytest tests/unit tests/api tests/integration -q`（真实模型依赖可用时激活项目要求的集成标记）；前端测试全跑。
- [ ] 新算法对100组全部处理，记录reference/scan/score/时间/候选质量/预算/耗时，重点核对解码边界、低分辨率、参考歧义、无脸。仅匿名元数据，不落地特征。
- [ ] 与前轮61+15+20的诊断可比数量对照；如有不同解释原因，不硬编码测试样本或瞒报失败。
- [ ] 确认所有未标定结果found=null、无PASS，真实图片/视频和原始敏感文件名未进入Git。
- [ ] 完整独立安全/规格/代码审查，重要问题清零；记录确切测试结果与限制。

## Task 7: 固定版本部署与回滚

Files: 经验证的既有远端部署目录；新增本地/仓库受审部署脚本和匿名发布报告。

- [ ] 提交并推送已验证的公司工作分支；远端按固定提交构建算法/控制台镜像，不使用浮动latest。
- [ ] 发布前记录18001/18002/18003容器ID和镜像、端口/挂载/限制；新配置从既有运行配置合并presence字段，保留实际启用MODNet及安全项，不用默认yaml覆盖。
- [ ] 临时候选只绑定loopback空闲端口，检查health/ready/OpenAPI、新接口及旧接口冒烟，核对内存/进程/临时文件/日志轮转。
- [ ] 保留旧镜像及停止后的旧容器；仅切换18002/18003，保持<server-37>绑定。失败立即停止新容器并恢复旧容器，不删除旧运行资产。
- [ ] LAN检查新接口100组或等效完整现场回归、控制台入口、旧接口健康；核对18001容器ID/镜像未变、无公网绑定。
- [ ] 发布报告包含新commit/image、测试结果、未标定状态、开发调用地址、OpenAPI/文档入口、回滚触发条件和准确命令。用户交付不包含密钥/env内容。
- [ ] 只有实际服务器验证成功才报告已上线；若受权限/网络/构建阻断，明确报告已完成部分与阻断，不伪称部署。
