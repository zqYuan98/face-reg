# TASK-2026-001 图片试用阈值实施计划

以 `../specs/2026-09-30-image-configured-threshold-design.md` 为已确认规格。既有隔离工作区、工作分支保持；主代理整合、提交及部署，三个子代理按不重叠文件并行测试先行。

## 1. 算法与配置（算法代理）

- 在 `tests/unit/runtime/test_config.py` 和 `tests/unit/verification/` 先加入 configured 配置、发行归属、上下边界和质量/系统错误优先级的失败测试，记录 RED。
- 修改 `face_algorithm/runtime/config.py`，新增严格 configured 分支；新增 `verification/configured_threshold.py` 受控不可变状态；在 `verification/policy.py`、`image_verifier.py` 接入。不改共享 runtime/calibration.py。
- 固定两个跨模块码：`IMAGE_TRIAL_THRESHOLD_MATCHED` / `IMAGE_TRIAL_THRESHOLD_NOT_MATCHED`；新状态 `CONFIGURED_UNVALIDATED`，配置阈值必须有限 [0,1]，分数 >= 阈值为 MATCHED，但业务始终 REVIEW。
- 回归旧 artifact、uncalibrated、1024维约束及不可用图片，记录 GREEN。保留源码默认 uncalibrated。

## 2. DTO 与 API（契约代理）

- 先补 `tests/unit/test_schemas.py`、对应 `tests/api/` 的新枚举、状态矛盾、序列化、固定上传字段/返回字段和旧视频契约测试，记录 RED。
- 修改 `face_algorithm/schemas.py`、`api/openapi.py`、`api/app.py`，只扩展图片状态、两个原因码和图片描述；版本2.4.0。必要时更正图片路由描述，不能加入 HTTP threshold 参数。
- configured 必须 threshold 非空、签名元数据空、REVIEW；有分数两侧必须 usable、原因码方向一致。质量 RECAPTURE 与硬错误优先，不能视作未匹配。
- 运行对应单测/接口测试，记录 GREEN。视频 CalibrationSummary 不扩展。

## 3. 测试台（前端代理）

- 在 `tools/visual-console/tests/core.test.mjs` 先测两种图片试用结果、无效/矛盾/伪造 PASS、质量失败与其他能力兼容，记录 RED。
- 仅改 `tools/visual-console/core.js`（必要时 app.js）：图片专属解释新状态及原因，显示“达到/未达到试用匹配阈值”、实际分数、阈值，始终人工复核。沿用原布局，不重新设计。
- Node 全套测试 GREEN；浏览器真实上传由主代理完成。

## 4. 部署安全（主代理）

- 只读确认37当前4e6a/API2.3.1、完整容器 ID 与配置SHA；快照 OpenAPI。18001不变。
- 部署脚本测试先行：固定旧4e6a完整ID及2.3.1回滚基线；只允许 image_verification.mode=配置模式、threshold=.55，其余完整配置必须相等；重复/歧义 YAML 拒绝。
- 修改 `scripts/deploy_presence_release.py` 与 `tests/unit/test_deploy_presence_release.py`，健康检查目标2.4.0并校验新增图片状态，保留安全标签/归属校验、候选18012和旧容器回滚。

## 5. 整合验收

- 合并工作区更改后独立规格审查，修复，再独立代码质量审查，修复。各代理不提交、不部署。
- 运行全量 pytest 与 Node，真实模型测试同图/不同图片/无效图片，检查阈值 .55、边界、原白底反馈和视频 .5。实际样本仅作为技术回归，不宣称准确率。
- 固定接口名、请求参数及所有返回字段名；比对旧 OpenAPI，仅允许指定新增枚举、版本和相关描述。
- 提交使用公司规范，推送现有工作分支。固定 commit archive 校验SHA；构建、候选验收后切换18002/18003，失败回滚原容器。禁止合并main、强推、标签或操作旧18001。
- 浏览器图片上传、结果阈值、重置及 console errors 检查；现场视频三对、白底反馈和六条v2回归；最终检查健康、配置与受保护容器不变。

## 6. 文档与交付

- 更新8个当前入口（根 README/API_DOCUMENTATION，API README，ALGORITHM_API_INTEGRATION、TASK-2026-001-API-DEBUGGING、TASK-2026-001-VALIDATION、PRESENCE_LABEL_VALIDATION、测试台 README）。新增本次报告，历史报告不改。
- 说明服务端配置/重启、部署 .55 与源码默认区别、完整试用JSON示例及新增枚举兼容要求；视频仍 .5，试用不是正式标定。
- 最后重新验证 Git 干净与远端提交、服务健康；简明汇报实际已完成和证据。

## 测试命令

PowerShell：设置 OMP_NUM_THREADS、OPENBLAS_NUM_THREADS、MKL_NUM_THREADS 为1。使用既有 `face-algorithm-modular/face-reg-platform/FaceFeatureExtractorAPI/.venv/Scripts/python.exe`。

```text
python -m pytest -q tests/unit/runtime/test_config.py tests/unit/verification
python -m pytest -q tests/unit/test_schemas.py tests/api
python -m pytest -q tests/unit/test_deploy_presence_release.py
python -m pytest -q
node --test tools/visual-console/tests/*.test.mjs
```

本轮修改前相关基线：359 passed、1 skipped。测试产生媒体/快照仅存被忽略输出目录，不提交用户照片、视频、密钥、.env或构建产物。
