# Video Presence Best-Effort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现低质量视频人脸关键帧实际比对、受控补采、试用阈值三态结果和可联调展示。

**Architecture:** 保留旧接口。presence 拆为质量/候选分析、流式采样、纯数值判定、运行时整合四层；HTTP 和测试台使用同一严格 DTO。未配置阈值仍输出证据，configured 明确为试用状态，不虚构正式精度。

**Tech Stack:** Python / Pydantic / FastAPI / NumPy / OpenCV / ONNX Runtime；原生 JavaScript 测试台；pytest 与 Node test。

---

## 上下文、执行约束与接口约定

批准规格：`docs/superpowers/specs/2026-09-28-video-presence-best-effort-design.md`。2026-09-28 用户明确“按文档实施”。沿用已隔离工作树 `C:/Users/24830/Documents/人脸识别/.worktrees/company-publish-prep/FaceFeatureExtractorAPI`，公司工作分支不变。基线提交 `800f0f5`，presence/decoder/API 基线测试 149 passed。

下文所有路径相对 API 根目录。Python 命令使用 PowerShell：

```powershell
$taskPython = 'C:\Users\24830\Documents\人脸识别\.worktrees\face-algorithm-modular\face-reg-platform\FaceFeatureExtractorAPI\.venv\Scripts\python.exe'
& $taskPython -m pytest tests/unit/presence -q
```

使用 @superpowers:test-driven-development：每任务先添加行为测试、确认预期失败、最小实现、运行回归、独立规格审查后进行质量审查。文件互不重叠的算法/解码/前端可并行，集成由主代理承担；不让并行代理修改同一文件或各自执行宽泛 git add。主代理统一精确暂存和提交，不提交媒体、标签、向量、密钥或构建产物。

以下约定使三个独立子任务可以并行：

- analyzer 保留 `PresenceAnalysis.features`，新增同长度 `feature_candidate_indices`、`feature_quality_warnings`，以及总 `quality_warnings` 和 `failed_candidates`。向量只在内部内存中，reason_codes 是阻断/局部失败而不是质量提示。
- decoder 新增 presence 专用请求 `PresenceDecodeRequest(sample_interval_seconds, max_supplemental_frames)`；保留原 `DecodeRequest` 的现有使用。新请求产出 `PresenceSampleFrame(index, timestamp_seconds, image, window_index, kind)`，kind 为 base/supplemental；末帧强制 base，按真实帧号去重。流上提供 `sampling_interval_seconds`。summary.sampled_frames 表示实际传输候选帧数，runtime.sampled_frames 表示实际分析数，不要求二者相等。
- runtime 按窗口基础帧的 `not features or quality_warnings or failed_candidates or candidate_limit_hit` 决定是否分析该窗口剩余帧；无触发则消费但不分析补采候选。所有传输和实际分析帧均不超过预算，最终完成要求消费完整流和基础计划完成。
- policy 新增 `mode=uncalibrated|configured`、`threshold: float|None`、`threshold_id: str|None`、`max_supplemental_frames=2`。configured 要求阈值在 (-1,1) 和非空 id；旧模式禁止携带阈值。指纹运行时从当前精确模型/策略计算，用于审计，不另建签名校准服务。
- 新 DTO 精确字段以规格 §6 为准；quality_warnings 为 ReasonCode 元组；新增 THRESHOLD_NOT_CONFIGURED、CANDIDATE_LANDMARK_INVALID、CANDIDATE_ALIGNMENT_INVALID、CANDIDATE_FEATURE_INVALID、CANDIDATE_COMPARISON_INCOMPLETE 原因码。quality 可以沿用 TOO_DARK/TOO_BLURRY 等已存在码。

## Task 1：配置、纯判定和严格 DTO（主代理）

Files: Modify `face_algorithm/presence/config.py`, `face_algorithm/presence/contracts.py`, `face_algorithm/schemas.py`; Create `face_algorithm/presence/decision.py`; Test `tests/unit/presence/test_contracts.py`, `tests/unit/presence/test_decision.py`。

- [ ] 添加测试：configured 缺阈值/id 或 NaN/布尔/越界拒绝；未配置不能夹带阈值；三态和 equality 边界、无脸、截断、局部失败、全局失败、未完成、未配置分别得到正确状态。
- [ ] 执行 `& $taskPython -m pytest tests/unit/presence/test_contracts.py tests/unit/presence/test_decision.py -q`，确认新模式/字段/判定缺失导致失败。
- [ ] 实现纯函数 `decide_presence(success, scan_complete, comparison_status, best_score, threshold, candidate_limit_hit, failed_candidates)`，顺序严格为：失败/未完成/无比较/无阈值 => INCONCLUSIVE；score >= threshold => FOUND；有截断/局部失败 => INCONCLUSIVE；否则 NOT_FOUND_IN_SCAN。
- [ ] DTO 校验 decision/found、comparison_status、阈值元数据、证据唯一/顺序/最佳项、base+supplemental=sampled、compared+failed<=candidate、版本/1024/隐私，禁止 PASS。旧无阈值响应允许迁移期间的默认字段，但新 runtime 必须显式填写准确新字段；不得为未知统计编造成功比较数量。
- [ ] 重跑上述命令至通过。独立审查后精确提交 `feat(video): TASK-2026-001 增加可配置试用阈值与三态契约`。

## Task 2：低质量候选分析（独立算法子任务）

Files: Modify `face_algorithm/presence/analyzer.py`; Create `face_algorithm/presence/quality.py`; Test `tests/unit/presence/test_analyzer.py`, `tests/unit/presence/test_quality.py`。不得修改公共 core 管线、config/contracts/runtime 或其他任务文件。

- [ ] 添加/更新测试：暗糊侧脸和小于64px仍提特征；边缘点可对齐不拒绝；退化五点/完全填充不得提特征；局部坏候选继续其他候选；非零有限1024要求和模型异常仍失败。
- [ ] 运行 `& $taskPython -m pytest tests/unit/presence/test_analyzer.py tests/unit/presence/test_quality.py -q`，记录预期红灯。
- [ ] presence 独立选择主脸与几何有效性，不借共享选择器重新引入旧几何质量门槛。质量评估仅产出警告并保留有效特征；分析结果按上方接口提供候选索引、警告和失败数量。
- [ ] 对明确局部无效输入返回局部原因；未知异常、推理会话异常、版本/维度错误全局失败。沿用对齐256协议及私有1024归一化特征，避免 traceback/向量 repr。
- [ ] 重跑测试和 `tests/unit/core`，独立规格及质量审查后由主代理提交该任务文件。

## Task 3：有界解码与基础/补采调度（独立解码子任务）

Files: Create `face_algorithm/presence/sampling.py`; Modify `face_algorithm/video/presence_decoder.py`; Test `tests/unit/presence/test_sampling.py`, `tests/unit/video/test_presence_decoder.py`。不得改旧 `video/decoder.py` 或 runtime。

- [ ] 添加测试：三帧窗口、低帧率去重、真实末帧、1/2极低预算、600秒预算、元数据偏差、不完整扫描、顺序和清理；旧 DecodeRequest 路径保持现有行为。
- [ ] 运行 `& $taskPython -m pytest tests/unit/presence/test_sampling.py tests/unit/video/test_presence_decoder.py -q`，记录失败。
- [ ] 实现纯规划：预算 B 先留末帧，最多 R=2 补采；容量不足时减少 R，窗口数不超过 floor((B-1)/(1+R))，实际间隔 >= 请求间隔且能覆盖估计时域；B=1 只允许首帧，实际不止一帧必须不完整，不能伪造首尾齐全。
- [ ] 新请求使用规划器，前向解码、按窗口目标产生有类型流式帧，末帧不重复且必为基础帧；在帧消费前即可观察实际基础间隔。所有候选数量受 B 限制，解码持续检查总预算和截止时间；不积累整视频数组。
- [ ] 实际流超出规划可安全返回不完整统计，输入大小/时长超限仍按 HTTP 资源错误协议。不把流消费完成等同于每帧比较。
- [ ] 重跑测试和已有 `tests/unit/video`；独立审查后提交任务文件。

## Task 4：运行时与 API 整合（主代理，依赖1/2/3）

Files: Modify `face_algorithm/presence/runtime.py`, `config/algorithm.yaml`, `api/openapi.py`, `api/app.py`（仅版本/必要契约）; Test `tests/unit/presence/test_runtime.py`, `tests/api/test_video_presence_api.py`, `tests/api/test_openapi_complete.py`。

- [ ] 写组合层测试：基础质量差触发补采、良好帧不补采、局部失败不中断、实际候选索引、三态、最高分保留、未完成不判身份。API 测试新 DTO 和错误清理不泄露内部数据。
- [ ] 先运行这些测试看见新行为失败，再整合。运行时显式构造准确计数、comparison_status、质量警告、阈值和指纹；无阈值用 THRESHOLD_NOT_CONFIGURED 而非无条件签名校准原因。
- [ ] summary 校验传输帧>=实际分析帧，并验证完整消费、真实末帧、顺序/时间/尺寸等；只接受可信类型的新采样帧，保留原测试注入缝，不增加 HTTP 自选解码器。
- [ ] 更新 YAML 仅 presence 区域并仍默认无阈值，不制造生产参数。API 版本提高为2.2.0，按原注册方式导出完整 OpenAPI。README 和文档在任务6统一更新。
- [ ] 运行 `& $taskPython -m pytest tests/unit/presence tests/unit/video tests/api -q`，独立审查后提交整合。

## Task 5：测试台真实三态展示（独立前端子任务，按固定 DTO 并行）

Files: Modify `tools/visual-console/core.js`, `app.js`, `index.html`（仅必要文案）; Test `tools/visual-console/tests` 的 Node 测试。不得修改服务器代理逻辑或其他任务文件。

- [ ] 新测试：有效 configured FOUND/NOT_FOUND 可渲染，无阈值显示比对已完成，无脸显示无法比较；旧响应继续历史安全含义，未知/伪造契约不可放行。
- [ ] 用 `node --test tools/visual-console/tests/core.test.mjs` 确认红灯；再实现与后端一致的守卫，不能用高分自行替换后端 decision。
- [ ] 主标题按 presence.decision/comparison_status，REVIEW 作为次级业务建议；显示分数、阈值/试用状态、补采统计和质量提示。liveness=false 不再显示失败；保留兼容字段解释。
- [ ] 证据时间通过已载入的本地 video 预览跳转，无外部媒体请求/服务器图片存储；所有未知字符串用 textContent，重置清理交互状态。
- [ ] 运行正确 Node 命令及 Python proxy 测试；独立审查后提交该任务文件。

## Task 6：现场回归、标签工具和文档（主代理，可委托只读统计）

Files: Create `scripts/evaluate_presence_labels.py`, `tests/unit/test_evaluate_presence_labels.py`, `docs/reports/2026-09-28-presence-best-effort-validation.md`; Modify `docs/TASK-2026-001-API-DEBUGGING.md`, `docs/ALGORITHM_API_INTEGRATION.md`, `docs/TASK-2026-001-VALIDATION.md`, `README.md`, `tools/visual-console/README.md`，以及仓库根 README/API_DOCUMENTATION 的直接相关描述。

- [ ] 为匿名标签/预测分离和评估写失败测试：unknown 不自动变 present，uncertain 不算准确率分母，重复/冲突标签拒绝，误报漏报计数按视频而非帧；不得自动推断跨组视频负例。
- [ ] 实现最小离线 CSV/JSON 验收工具与空白格式说明，分开输出覆盖率/判定覆盖和已标注误报漏报；没有标签时仅做功能统计，不自动选生产阈值。
- [ ] 用现有忽略目录脚本 `.analysis/video-presence-20260928/presence_release_probe.py` 在本地新代码上完整跑100组原素材，保留匿名完整新 DTO，用独立输出文件，不覆盖历史报告。实际出现标签全部未知直到人工提供。
- [ ] 更新全部开发联调文档和JSON示例：三态、新枚举、comparison_status、证据与计数、试用阈值配置方法、无阈值含义、错误与资源限制、旧接口语义，标记2.2.0当前尚未部署。
- [ ] 全回归 `& $taskPython -m pytest tests/unit tests/api tests/integration tools/visual-console/tests -q`，加Node测试。启动仅回环的本地 API/console，用 Playwright 验证真实照片/视频提交、证据跳转与无阈值提示；合成配置用来测试三态，不充当真实阈值。
- [ ] 最终独立规格审查通过后再做质量审查；修复问题、重跑受影响测试。遵循 @superpowers:verification-before-completion，报告真实功能/现场验证/上线三个层次，不在缺人工标签时宣布识别精度完成。

## 收尾与部署边界

- [ ] 检查 git diff --check、精确变更集合、没有媒体/向量/凭据，提交公司规范信息。保留工作分支，不合并main、不自动创建PR。
- [ ] 本轮完成代码和本地验证；线上37服务器仍为旧版，除非后续明确进入发布步骤。现有上线约束：仅18002/18003、候选测试、保留回滚、18001不动、实际MODNet配置不覆盖。
- [ ] 使用 @superpowers:finishing-a-development-branch 收尾，保留本工作树供用户继续，不清理其他更改。
