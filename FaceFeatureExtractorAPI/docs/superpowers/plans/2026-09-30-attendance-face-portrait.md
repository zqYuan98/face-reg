# Attendance Face Portrait Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付考勤白底人脸照并受控上线 37 的 18002/18003，保持原有 API 请求和响应字段。

**Architecture:** 默认 face_archive 策略仅放宽 portrait 的审美/构图门槛，保留真实像素、有效几何及抠图硬校验。共享检测管线提供显式 portrait-only 质量入口，严格质量路径不变；生成器组合提醒，客户端根据 success 和图片判断出图。部署从当前 d78e723 版本迁移，只修改 portrait mode/version，保留视频阈值 0.5、MODNet 及所有其他运行参数。

**Tech Stack:** Python / NumPy / OpenCV / Pydantic / FastAPI / MODNet，原生 JavaScript 测试台，pytest / Node test，Docker。

---

用户于 2026-09-30 请求“上线服务器，接口不变吧”，批准实施和发布此前确认的设计。使用已有干净工作树 company-publish-prep 和公司工作分支，不新建分支、不合并 main。用户已选择模块分工并行；各实现者独占文件，主代理整合并串行提交，避免 Git 索引竞争。

依据：`docs/superpowers/specs/2026-09-29-attendance-face-portrait-design.md`。
工作目录为仓库的 `FaceFeatureExtractorAPI`。下文 `$portraitPython` 指：
`C:\Users\24830\Documents\人脸识别\.worktrees\face-algorithm-modular\face-reg-platform\FaceFeatureExtractorAPI\.venv\Scripts\python.exe`。
所有本地编辑用 apply_patch，媒体和部署包只写忽略目录。

## Task 1: 显式 portrait-only 质量入口（质量模块所有者）

Files: `face_algorithm/core/pipeline.py`、`face_algorithm/schemas.py`、`tests/unit/test_pipeline.py`（实际相邻 pipeline 测试文件及新的 `tests/unit/test_portrait_quality.py`）。

- [x] 先写测试并运行 `& $portraitPython -m pytest tests/unit/test_portrait_quality.py -q`；新增入口不存在/边距仍拒绝时应失败。
- [x] 新增 `FacePipeline.analyze_portrait_quality(image, profile)`，仍返回 `FaceQualityAnalysis`，禁止提取 embedding。复用现有管线的检测、选脸、五点几何、对齐、模型调用，仅对该入口启用隔离策略。
- [x] 完整验证原始框有限、有效且在图内，五点实际有效且在图内后，原 2% 边距命中转换为 `PORTRAIT_SOURCE_LIMITED`，继续计算质量。不能把泛化 FACE_CROPPED 统一放行。
- [x] 质量 TOO_DARK/TOO_BRIGHT/TOO_BLURRY/BAD_POSE 作为成功分析 + REVIEW 提醒返回，保留 corrected_image/box/landmarks；真实越界、低于 112px、模型异常等硬原因仍阻断。添加 `PORTRAIT_SOURCE_LIMITED` 和 `PORTRAIT_UPSCALE_LIMITED` 枚举。
- [x] 测试各提醒、组合、真实越界、无效关键点、推理异常、不调用 embedding、旧 analyze_quality/analyze_trusted 仍严格；运行现有 pipeline/selection 测试。
- [x] 交付红/绿结果与独占文件清单，主代理完成需求审查和质量审查后统一提交。

实证：新入口测试先 RED（92 项因缺少入口/枚举失败），实现后质量入口、core 与 schemas 合计 260 passed；已完成需求/代码质量交叉审查。

## Task 2: 归档构图、生成结果和配置（portrait 模块所有者）

Files: `face_algorithm/portrait/{generator,contracts,template,subject_validator}.py`、`face_algorithm/runtime/config.py`、`config/algorithm.yaml`、`tests/unit/portrait/test_{generator,subject_validator}.py`、新 `tests/unit/portrait/test_face_archive.py`、配置测试中相关断言。

- [x] 先写配置/DTO/无肩/小脸测试，运行 `& $portraitPython -m pytest tests/unit/portrait/test_face_archive.py -q`，记录预期失败。
- [x] 配置 mode 为 Literal[face_archive,head_shoulders]，缺失默认 head_shoulders；仓库配置显式 face_archive、version=face-archive-480x640-v1。旧严格测试夹具明确 head_shoulders，不弱化断言。
- [x] face_archive 绑定 Task 1 的 analyze_portrait_quality；严格模式仍绑定 analyze_quality。仅白名单软提醒可继续生成；任一硬原因优先失败且无派生图。
- [x] 归档模式只保留原始 112×112 限制，取消约 205px 隐含脸高；抠图核心和脸覆盖阈值不变，肩部按实际可见范围测量（可为 0）但不拒绝；局部头顶受限产生 SOURCE_LIMITED。
- [x] 自适应等比例构图：scale=min(理想脸高/真实脸高,max_upscale_ratio,输出宽/脸宽,输出高/脸高)，按眼部理想位置计算平移并夹紧，使完整脸框位于画布；尽量保留局部头部，不强制肩部/头顶留白。缩放达不到理想时加入 UPSCALE_LIMITED。不得修补源像素，仍使用真实 alpha 替换源背景。
- [x] PortraitResult 成功要求 image/placement/matting_quality 齐全：空理由只允许 PASS，白名单非空只允许 REVIEW；失败无派生图/placement/matting_quality，且不能仅有新增两种提醒。严格模式下旧审美原因本来就是硬拒绝，保留其 RECAPTURE 兼容；归档 generator 保证失败有真实硬原因，不追加虚假原因。不修改其他 DTO。
- [x] 测试无肩、贴边、部分头发缺失、112–204px、宽窄脸、自适应边界、最大放大、无拉伸、坏 alpha/脸漏抠/异常、肩部指标真实、编码 REVIEW 图片与严格旧规则。运行 `& $portraitPython -m pytest tests/unit/portrait -q`。
- [x] 交付改动与结果，需求/质量审查后主代理统一提交 `feat(portrait): TASK-2026-001 支持考勤白底人脸照和质量提醒`。

实证：配置、DTO 和归档构图已完成 RED/GREEN；可靠触顶提醒补漏先 RED，修复后 portrait 全目录 225 passed，独立复审 Approved；严格模式和低覆盖细发行为不变。

## Task 3: API、测试台和文档（集成所有者）

Files: `api/app.py` 版本、`api/routes/v2.py` OpenAPI 描述、`tests/api/test_v2_api.py`、`tests/api/test_openapi_complete.py`、`tools/visual-console/{core,app}.js`、`tools/visual-console/index.html` 及 `tools/visual-console/tests/core.test.mjs`、API/开发/部署入口 Markdown 文档。

- [x] 先补成功 REVIEW 仍带图片的 API 序列化/前端模型测试；固定请求只有 image、响应字段与旧版本一致。运行针对性 Python 和 `node --test tools/visual-console/tests/core.test.mjs` 观察失败。
- [x] API version 更新 2.3.0；不新增 route、request mode 或 quality_warnings 字段。保留 480×640 JPEG 与所有 shoulder 字段。
- [x] 测试台“白底人脸照”，成功 REVIEW 展示“已生成，存在质量提醒”并可下载；新增两个原因码中文释义。更新接口说明，提醒 success/image 判断、原图归档及考勤联动由业务负责，PASS 不代表身份通过。
- [x] 更新当前文档入口，不改写历史验收报告；保留视频 0.5 说明。运行完整 pytest 和 Node 测试；修复集成回归后需求/质量审查。

实证：API/前端先 RED 后 GREEN，需求和代码质量审查通过。最终代码 `2557e1707eb77b7cf7a077541030d7ec64f5ae84` 的 fresh 全量回归为 1598 passed / 1 skipped / 4 条已知 warning（122.97s），Node 33 passed。六接口 OpenAPI 独立对照仅四处差异：info.version、portrait summary、portrait description、ReasonCode 新增两值；请求/响应字段、类型和结构均未变化。

## Task 4: 固定版本发布与回滚保护（部署所有者）

Files: `scripts/deploy_presence_release.py`、`tests/unit/test_deploy_presence_release.py`；部署验证脚本和结果置于工作区忽略目录 `.analysis/portrait-release-20260930`。

- [x] SSH StrictHostKeyChecking=yes 只读核对当前 18001/18002/18003 身份、健康、资源/配置 SHA 与当前 presence 0.5。发现其他人已替换或配置变化即暂停发布并报告，不覆盖。
- [x] 先补迁移测试：OLD_COMMIT=d78e723b8fb8737b09e12859e57aa9856a640db6；仅迁移 portrait mode/version，其他 YAML 数据完全相同（含所有 presence、MODNet）；异常/重复 YAML 拒绝。运行 `& $portraitPython -m pytest tests/unit/test_deploy_presence_release.py -q` 观察失败。
- [x] 更新 helper 固定基线、2.3.0 readiness、portrait 契约检查和精确迁移验证；原子保存状态、备份 d78 容器和配置，回滚恢复 d78 + 阈值 0.5。不动 18001/更早备份。
- [x] 提交前运行全套测试、git diff --check 和敏感/媒体范围审查；按公司格式提交，正常尝试 push 公司工作分支，认证失败如实记录且不探查凭据。生成 git archive 固定完整 SHA 并核验 SHA256，SCP 到公司服务器，离线构建。
- [x] 启动 127.0.0.1:18012 candidate，先验证 2.3.0/六接口/新 portrait 和 threshold 0.5；不通过不得 promote。
- [x] 固定 seed=20260930 从 download_images 抽取 20 张，另对既有单人参考图生成无肩/贴边/小脸输入，真实模型检查并人工看输出（只评图像处理不识别人）。媒体仅本地忽略目录；记录技术生成率/失败原因，不当识别准确率。
- [x] candidate 验收成功后受控切换 18002/18003，失败自动回滚。检查 LAN API、浏览器图像展示/下载、版本、presence 0.5、无临时残留、18001 ID/image/start/restart 不变。
- [x] 写匿名发布报告，保存回滚命令，更新任务勾选；提交文档并再次 push。最终明确接口不变及 REVIEW 兼容提醒、访问地址、验证结果与 Git 同步真实状态。

实证：最终候选 26 个图片请求全部 HTTP 200、22 个出图（随机17/20、针对输入5/5、空白0/1）；六接口及 MP4/AVI/MOV 冒烟通过后于北京时间09:58正式切换。LAN重跑六接口、三个现场presence回归对与浏览器REVIEW预览/下载成功；最终8/8模型ready、临时文件0、18001不变、d78旧容器保留。早期大图超时及同阶段旧版复现仍记录为风险，不因最后复测成功抹去。部署身份、回滚命令和边界见 `docs/reports/2026-09-30-attendance-face-portrait-release.md`。

## Review and completion gates

- [x] 计划独立审查通过（2026-09-30 Approved）；基线 1266 passed / 1 skipped。
- [x] 每个模块先需求审查，再代码质量审查；修正重要问题后复审。
- [x] 完整回归和真实 candidate 验证通过后才能切生产。
- [x] 生产新证据确认成功，使用 verification-before-completion 和 finishing-a-development-branch 完成收尾；不自动开 PR、合并或删除现有工作树。
