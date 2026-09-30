# Portrait Box Overflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking.

**Goal:** 修正白底归档对轻微检测框越界误拒绝，原图可用且接口不变，验证后上线37。

**Architecture:** portrait专用纯函数定义5%每侧/90%可见面积预算；仅portrait quality调用，仍经原selector五点几何和蒙版检查。非portrait严格入口不变；API补丁2.3.1。发布脚本固定2557基线、配置原字节保留。

**Tech Stack:** Python/NumPy/OpenCV/FastAPI/MODNet、pytest/Node、Docker及Playwright。

---

用户已回复“实施”确认前一轮提出的修复及上线，不重新开启方案选择。独立spec审查Approved。沿用干净隔离worktree `C:/Users/24830/Documents/人脸识别/.worktrees/company-publish-prep` 和公司 `feat/TASK-2026-001-modular-face-algorithm-api` 分支，无main操作。既有1598/1skip和Node33基线；开始前重跑相关基线。各任务独占文件可并行，主代理统一Git提交/推送/部署。

API目录为该worktree下 `FaceFeatureExtractorAPI`；下文Python均用 `C:/Users/24830/Documents/人脸识别/.worktrees/face-algorithm-modular/face-reg-platform/FaceFeatureExtractorAPI/.venv/Scripts/python.exe`。
规格：`../specs/2026-09-30-portrait-box-overflow-design.md`。

## Task 1：portrait边界策略与流水线

Files: 新 `face_algorithm/core/portrait_bounds.py`、改 `face_algorithm/core/pipeline.py`；测试 `tests/unit/test_portrait_quality.py` 与新 `tests/unit/core/test_portrait_bounds.py`。不得改共享selector规则。

- [x] 写失败测试：实际717×720/框底737场景的合成几何、四侧轻微溢出、恰5%及超5%、可见面积恰90%及以下；先运行pytest观察原FACE_CROPPED导致期望REVIEW失败。
- [x] 纯函数计算overflow tuple=(max(0,-x1)/w,max(0,-y1)/h,max(0,x2-W)/w,max(0,y2-H)/h)，保留面积=clip_w*clip_h/(w*h)；有限正框、各侧<=.05且ratio>=.90才允许。拒绝非有限、空框/无交集，浮点边界不得人为放宽宏观阈值。
- [x] 接入已成功selection之后、原box越界分支；portrait允许有限溢出并加入SOURCE_LIMITED。明示五点有限/形状/几何及图内条件必需；归档分辨率改用可见框，strict原始框行为不变。后续public.box沿用图内框，不改原始像素/landmarks。
- [x] 更新与新设计冲突的旧portrait“任意微小溢出失败”测试为严重溢出失败，同时保留原微小溢出数据验证strict仍拒绝。新增裁后不足112、关键点坏/越界、歧义、异常和noembedding回归；不删除保护断言。
- [x] 运行 `python -m pytest tests/unit/test_portrait_quality.py tests/unit/core tests/unit/portrait -q` GREEN；自查后独立需求审查、代码质量审查，主代理统一提交。

## Task 2：发布保护与版本

Files: `scripts/deploy_presence_release.py`、`tests/unit/test_deploy_presence_release.py`；主代理负责 `api/app.py`、`tests/api/test_openapi_complete.py`。

- [x] 发布测试先RED：基线固定2557完整SHA、2.3.0旧版ready；新wait_http要求2.3.1。原配置拷贝字节/SHA不变，候选wrong-version拒绝，回滚校验旧2.3.0并保留资源/端口保护。
- [x] 更新helper固定OLD_COMMIT/原容器IDs/配置SHA为fresh audit；配置原样拷贝而非重新序列化，完整校验相等、face_archive、MODNet启用与presence .5/ID保留。legacy migration函数若保留供历史单测不得用于新build。
- [x] 回滚目标为2557旧API与console容器。保留18001和更早备份，不篡改旧服务器helper。运行 `python -m pytest tests/unit/test_deploy_presence_release.py -q`，需求/质量双审查。
- [x] 先改OpenAPI版本测试为2.3.1并见RED，然后只改应用版本；不动字段/枚举/路由或console业务代码。

## Task 3：整合、真实验收与上线

Files: 原图诊断与回归脚本仅工作区 `.analysis/portrait-crop-feedback-20260930/`；更新当前API/README说明及新 `docs/reports/2026-09-30-portrait-box-overflow-release.md`，旧报告不改。

- [x] 复用授权原图、当前模型和MODNet本地生成，检查REVIEW+SOURCE_LIMITED+480×640 JPEG；人工看输出，记录匿名数值，媒体不进Git。严重裁切/坏关键点拒绝由确定性测试证明，不用模型猜测作真值。
- [x] 本地完整 `python -m pytest -q tests tools/visual-console/tests` 和 `D:/nodejs/node.exe --test tools/visual-console/tests/core.test.mjs`，git diffcheck/文件范围审查，独立最终代码审查。
- [x] 公司格式提交、push；git archive固定完整commit、SHA256；StrictHostKeyChecking=yes上传、离线构建，候选127.0.0.1:18012。确保基线IDs/config未变，不盲目覆盖。
- [x] 候选先原图（要求出图）和严重无脸负例；再同seed20随机+前版6针对输入、6路由/MP4 AVI MOV冒烟。OpenAPI深度对照应仅版本差异，presence .5不新增活体门禁。大图504记录并等待后台结束后有限复测，不扩大timeout或并发压测。
- [x] 验收通过后promote，旧2557原容器停止保留；LAN原图+六接口+3现场presence回归对，Playwright测试台预览/下载、重置；最终健康/临时文件0/18001身份不变。
- [x] 记录运行代码/配置/镜像/容器/测试/风险/回滚命令；8个当前文档入口更新2.3.1、差异规则及新报告链接，历史记录保留。文档独立复核、提交推送，关闭本次临时代理和隧道/浏览器，留分支worktree无PR/merge/tag。

## Gates

- [x] 用户设计批准与独立spec审查。
- [x] 独立plan审查及任务分工。
- [x] 模块RED/GREEN、需求/质量双审查。
- [x] 本地完整回归与候选实测通过。
- [x] 正式发布新证据、文档/Git同步与回滚保留。

本地阶段证据：原图生成成功REVIEW/SOURCE_LIMITED、480×640 JPEG（recognizer idle），人工检查完成；完整pytest 1749 passed / 1 skipped（127.64秒），Node33；独立需求与代码质量审查均Approved，无阻塞项。

发布阶段证据：固定运行4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a，2026-09-30北京时间14:22上线2.3.1。
候选26固定输入22出图/4无脸，另反馈原图成功，六接口与三视频容器通过；正式重复原图、六接口、3现场回归对和浏览器预览/下载通过。
8个入口文档及新报告独立复核Approved，54个相对链接有效；详细证据、配置/镜像/容器身份、风险和回滚见[发布报告](../../reports/2026-09-30-portrait-box-overflow-release.md)。
