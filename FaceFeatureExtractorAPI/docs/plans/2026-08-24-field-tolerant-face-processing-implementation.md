# TASK-2026-001 现场容错人脸处理实施计划

日期：2026-08-24

状态：执行中

依据：[现场容错人脸处理设计](2026-08-24-field-tolerant-face-processing-design.md)

## 1. 基线与不可破坏约束

实施前基线：

- Python：`717 passed`，覆盖 `tests/unit`、`tests/api` 和测试台服务测试；
- 测试台前端：`14 passed`；
- 当前分支：`feat/TASK-2026-001-modular-face-algorithm-api`；
- 特征契约：`recognizer_v1_1024`，维度固定为 1024；
- 候选服务：18002，测试台：18003，旧服务 18001 不修改。

不可破坏约束：

1. `entry_verify`、`contract_reference`、`contract_video_frame` 和旧接口保持严格单脸。
2. 五个 v2 路由、multipart 字段和现有公共 JSON 字段保持兼容。
3. 人证成功结果的 `reason_codes` 必须为空；`CARD_*` 不得作为成功告警混入。
4. 不提交现场图片、原始文件名、派生图片、特征向量、密钥或构建产物。
5. 每个模块先写失败测试，再做最小实现；跨模块联调前各自单测必须通过。

## 2. 实施顺序和依赖

### 阶段 A：配置与主脸选择内核

目标：提供所有后续能力共享的确定性候选选择模块，并保持严格能力原语义。

涉及文件：

- 新增 `face_algorithm/core/face_selection.py`；
- 修改 `face_algorithm/runtime/config.py`；
- 修改 `config/algorithm.yaml`；
- 修改 `face_algorithm/core/adapters.py`；
- 修改 `face_algorithm/core/pipeline.py`；
- 修改 `face_algorithm/runtime/calibration.py`（仅在现有 profile 指纹未自动覆盖新联合配置时）；
- 新增 `tests/unit/core/test_face_selection.py`；
- 修改 `tests/unit/core/test_pipeline.py`、`tests/unit/core/test_adapters.py`；
- 修改 `tests/unit/runtime/test_config.py`、`tests/unit/runtime/test_calibration.py`。

实施步骤：

1. 先为 `ExactOneFaceSelection` 与 `DominantPrimaryFaceSelection` 写严格配置测试：
   - 旧 `require_single_face` 被拒绝并给出迁移错误；
   - portrait/witness 只能使用 `dominant_primary`；
   - verify/contract/video 只能使用 `exact_one`；
   - 严格配置不接受 dominant 专属阈值；
   - 阈值顺序和取值边界受校验。
2. 为纯选择器写表驱动测试：
   - 候选规范化、稳定排序和输入排列不变性；
   - 第 6 个饱和候选 fail-closed；
   - `<0.10`、`0.10~0.20`、`>=0.20` 边界；
   - 无有效主脸、关键点调用异常、低置信大误检；
   - 可选排除区域覆盖次脸 80% 的 witness 上下文。
3. 将 detector 上限从 2 改为有界 6；严格模式仍按 0/1/至少2处理。
4. 在 pipeline 中接入选择器，并复用已计算的主脸五点，避免主脸重复推理。
5. 只有选中主脸才进入对齐、姿态、质量与特征提取；歧义时断言 embedder 未调用。
6. 升级 `config_version`，确认 profile 指纹随模式、选择器版本和阈值变化。

阶段验收：

- core/runtime 相关单测全部通过；
- 原有严格双脸测试继续返回 `MULTIPLE_FACES`；
- 1024 维特征测试无变化。

### 阶段 B：白底照主体有效性

目标：允许非必要人物边缘触边，同时保留真实头脸/肩部裁剪拦截。

涉及文件：

- 新增 `face_algorithm/portrait/subject_validator.py`，或将同等纯函数从
  `template.py` 中独立出来；
- 修改 `face_algorithm/portrait/template.py`；
- 修改 `face_algorithm/portrait/generator.py`；
- 修改 `face_algorithm/runtime/config.py`、`config/algorithm.yaml`；
- 修改 `tests/unit/portrait/test_generator.py`；
- 新增或修改 `tests/unit/portrait/test_subject_validator.py`；
- 修改 `tests/integration/test_portrait_generation.py`。

实施步骤：

1. 为设计规格中的整数 ROI、顶部占用、稳健顶部、头顶空间和肩部三项覆盖规则写边界测试。
2. 将原软蒙版整体触边门禁替换为 `PortraitSubjectValidator`：
   - 软尾、衣服、身体或头发非必要部分触边允许；
   - 头顶空间不足且硬核心有效触顶时拒绝；
   - 肩部整体、左右半区或有效行覆盖不足时拒绝。
3. `required_face_height = ceil(output_height * face_height_ratio / max_upscale_ratio)`；
   在 matting 前以 `FACE_TOO_SMALL` 拒绝，消除 112~205 像素配置断层。
4. 放置后几何使用稳健顶部边界，不再使用软蒙版最小坐标。

阶段验收：

- 原软 alpha 尾部测试从 `SUBJECT_CROPPED` 改为成功；
- 真实头顶裁剪与必要肩部缺失仍失败；
- 480×640 白底结果及编码契约不变。

### 阶段 C：人证人物保护与可选证卡

目标：证卡检测不再阻止出图，人物保护和隐私边界均可量化。

涉及文件：

- 新增 `face_algorithm/witness/subject_selector.py`；
- 新增 `face_algorithm/witness/protection.py`；
- 修改 `face_algorithm/witness/blur.py`；
- 修改 `face_algorithm/witness/card_detector.py`；
- 修改 `face_algorithm/witness/runtime.py`；
- 修改 `face_algorithm/witness/contracts.py`（只在内部契约确有需要时，不新增公共状态字段）；
- 修改 `face_algorithm/runtime/config.py`、`config/algorithm.yaml`；
- 新增 `tests/unit/witness/test_subject_selector.py`；
- 新增 `tests/unit/witness/test_protection.py`；
- 修改 `tests/unit/witness/test_blur.py`、`test_card_detector.py`、`test_runtime.py`；
- 修改 `tests/integration/test_witness_blur.py`。

实施步骤：

1. 用表驱动测试固化 anchored/no-face 人物组件决策：50% 脸覆盖、35% 次组件比例及失败码。
2. 将 witness 从完整身份质量 pipeline 解耦，只复用输入校验、旋转、候选检测和主脸选择；
   无脸但单一可靠人物组件时允许继续。
3. 实现人物包络：
   - 单洞 5%、洞合计 8%；
   - 有界 closing 半径；
   - 新增面积同时受图像 8% 与人物核心 25% 限制；
   - 人物软保护不超过 85%，最终软保护不超过 92%。
4. `_CardDetectionResult` 内部分离 `cards` 和 `protection_cards`；保守候选受数量、
   单卡面积、总面积、人物接触和最终保护预算约束。
5. 所有 `CARD_*` 与 card detector 异常均降级为人物保护出图；只有 matting、
   多主体歧义、输入或推理硬错误继续失败。
6. 在 witness 中以人物/证卡上下文重新判定次脸；证件印刷头像不得阻止出图。
7. 高频背景测试验证保护区外 blur 能量下降；纯色背景不以像素差误拒绝。

阶段验收：

- `CARD_NOT_FOUND`、`CARD_TOO_SMALL`、`CARD_CROPPED`、
  `CARD_DETECTION_UNCERTAIN` 和 detector 异常均返回成功派生图；
- 保护面积越界、近全图人物或两个同级人物仍失败；
- 人物前方、落在包络或保守证卡候选内的证卡保持清晰。

### 阶段 D：25MP 资源路径与 witness 编码

目标：在资源验收通过时接受 25MP；否则自动保留 16MP 安全上限。

涉及文件：

- 修改 `face_algorithm/portrait/modnet.py` 或增加 witness 工作尺寸 matting 路径；
- 修改 `face_algorithm/witness/blur.py`、`runtime.py`；
- 修改 `api/serialization.py`、`api/routes/v2.py`；
- 修改 `tests/api/test_v2_api.py`；
- 新增或修改 witness 边界资源测试；
- 修改 `docs/witness-blur-deployment.md`。

实施步骤：

1. 禁止全尺寸 float matte/labels，工作图最长边限制 1600；坐标映射保持确定性。
2. 最终蒙版按最多 256 行条带上采样合成，限制同时存在的全尺寸 BGR 副本。
3. 仅 witness 编码使用质量 `92 -> ... -> 76` 的确定性阶梯；释放失败缓冲。
4. 最低质量仍超 15 MiB 时返回 413 + `IMAGE_TOO_LARGE`，不得返回 500。
5. 对 25MP 边界图测量：
   - 项目可见静态数组不超过 384 MiB；
   - 相对模型常驻基线增量 RSS 不超过 1.1 GiB；
   - witness-only 总 RSS 不超过 1.75 GiB；
   - 30 秒接口时限内完成。
6. 全部通过才把 `max_witness_pixels` 提升到 25MP；否则配置保留 16MP并记录实测阻塞。

### 阶段 E：测试台、文档与消费者契约

涉及文件：

- 修改 `tools/visual-console/core.js`、`app.js`；
- 修改 `tools/visual-console/tests/core.test.mjs`；
- 修改 `tools/visual-console/README.md`；
- 修改 `docs/ALGORITHM_API_INTEGRATION.md`；
- 按最终资源门槛修改测试台 server 限制与测试。

实施步骤：

1. witness 成功且 `card_regions` 非空：显示“可信证卡已识别，人物与证卡已保护”。
2. witness 成功且 `card_regions` 为空：显示“人物保护已完成，证卡识别非必需”。
3. 不根据公共响应猜测 `protection_cards` 或内部 `DEGRADED` 状态。
4. 若 25MP 资源门槛通过，同步移除测试台 16MP 限制；否则页面继续准确显示 16MP。
5. 更新集成文档：JSON 结构兼容，但 witness `success=true` 不再保证
   `card_regions` 非空。

## 3. 验证矩阵

每个阶段完成后运行最小相关集；全部合并后运行：

```powershell
python -m pytest tests/unit tests/api tools/visual-console/tests -q
python -m pytest tests/integration -q
npm run test:unit
```

真实模型和本地素材回归：

1. 使用固定种子 `20260824` 复用同一批匿名随机图片；
2. 不记录或输出文件名，只统计 suggestion/reason 分布；
3. 对变为 PASS 的派生图人工检查人物轮廓、背景虚化和证卡清晰区域；
4. 对仍失败图片区分真实不合格与算法误拒；
5. 使用合成双人、证件 inset、主体触边、无脸单人物和 25MP 边界图补足回归。

生产发布前另需客户授权标注集满足设计规格中的多人漏拒绝和证卡误拒阈值。

## 4. 提交与发布

建议按可回滚能力拆分提交：

1. `feat(core): TASK-2026-001 新增可配置主脸选择策略`
2. `feat(portrait): TASK-2026-001 放宽非必要主体触边判定`
3. `feat(witness): TASK-2026-001 实现人物优先背景虚化`
4. `fix(api): TASK-2026-001 完善人证大图编码与资源边界`
5. `feat(test-console): TASK-2026-001 更新人证保护结果提示`
6. `test(algorithm): TASK-2026-001 增加现场容错回归测试`
7. `docs(algorithm): TASK-2026-001 更新集成与部署说明`

完成后：

1. 推送当前公司工作分支；
2. 构建带 commit 标识的新候选镜像；
3. 原子更新 18002 算法服务和 18003 测试台；
4. 验证 readiness、五项页面操作和固定随机回归；
5. 保留 18001，不改旧服务和旧镜像。
