# TASK-2026-001 图片比对独立试用阈值

2026-09-30。用户要求 `/api/v2/verify/images` 设为 0.55；在已说明“保留请求及字段、扩展试用状态/原因码、有效分数按阈值比较、业务仍 REVIEW、视频保持 0.5”的方案后，用户明确要求“如果没有就补充代码”。按该已确认范围实施开发、验证并上线37，不重复要求选择执行方式；沿用用户已选择的模块分工。

## 事实和选择

当前2.3.1图片配置仅支持 uncalibrated/artifact。未标定 DTO 禁止非空 decision_threshold，signed artifact 才能 ACTIVE；直接添加 YAML 阈值会被严格校验拒绝。选用图片专属 configured 试用模式，不伪造签名标定包、不增加新接口或请求阈值覆盖。另一个方案是取得真实标定证据再使用 artifact，本轮不做。保留字段集合而非增加 is_same_person，避免把低分或不可比误读为身份否定；消费者须支持新增枚举。

## 配置与模块隔离

服务端新增图片配置分支，必填有限数值 threshold，范围[0,1]，拒绝bool/字符串/NaN/Infinity和缺失值。37设为：

```yaml
image_verification:
  mode: configured
  threshold: 0.55
  reference_quality_profile: entry_verify
  probe_quality_profile: entry_verify
```

源码通用默认仍为uncalibrated；启用由部署显式配置。每次更改配置需重启/重建绑定的服务，不支持HTTP上传参数或热覆盖。独立模块 `verification/configured_threshold.py` 负责不可变、受控创建并可校验归属的图片试用状态；现有共享 CalibrationState/CalibrationStatus/签名loader和quick/full不扩展。图片builder根据mode绑定独立试用状态或原签名状态。继续持有原模型/质量策略快照，不能通过试用绕过特征维度、模型版本、会话归属和strict质量校验。

## 后端判定和响应

API版本2.4.0；路由、reference_image/probe_image multipart参数和所有现有顶层/嵌套字段名不变。
图片 `calibration.status` 增 `CONFIGURED_UNVALIDATED`；共享ReasonCode仅新增：
`IMAGE_TRIAL_THRESHOLD_MATCHED`、`IMAGE_TRIAL_THRESHOLD_NOT_MATCHED`，不加入任何质量错误集合。
配置模式返回 `decision_threshold=0.55`、calibration_id=null、artifact_sha256=null，绝不宣称ACTIVE。

| 场景 | success / suggestion | 分数与原因 |
|---|---|---|
| 两侧有效、分数>=阈值（含等号） | true / REVIEW | 有分数；仅IMAGE_TRIAL_THRESHOLD_MATCHED |
| 两侧有效、分数<阈值 | true / REVIEW | 有分数；仅IMAGE_TRIAL_THRESHOLD_NOT_MATCHED |
| 质量不合格（原无脸/小脸/裁切等） | 沿用true / RECAPTURE | score=null；原质量原因，不含试用匹配码 |
| 系统/特征等失败 | 沿用false / REVIEW | score=null；原错误原因，不含试用匹配码 |

硬错误、质量问题优先于阈值，不能凭空造分数或把失败当未匹配；configured不返回PASS。不可用的内部阈值状态须fail closed或启动失败，不得构造假ACTIVE或退化成任意可比分数。域模型与HTTP序列化必须验证状态、分数、原因和两侧usable一致，严格拒绝矛盾原因、两码同时出现、阈值不合法、假标定元数据、伪造PASS、不可比携带匹配码。
原uncalibrated仍返回原THRESHOLD_UNCALIBRATED；原artifact的>=PASS和低于阈值REVIEW语义完全保留。

两个试用码只能出现在 configured、有有效分数且两侧 usable 的顶层结果；不可出现在任何侧的 reason_codes、旧标定模式或失败兜底分支。非法配置在启动时拒绝；若运行中内部状态失效，完整 runtime→HTTP 必须明确失败（可返回500或一致的结构化失败），不能保留可比分数而构造矛盾DTO。

## 测试台

仅图片能力解释新状态。主标题“达到试用匹配阈值”或“未达到试用匹配阈值”，同时展示0.5500和实际相似度，并注明“试用阈值，精度未验证，业务仍需人工复核”，canTreatAsPass=false。
结论由后端互斥原因码提供，UI只校验方向一致，不自行替代后端作决定；矛盾/伪造/未知试用响应显示不可用。
不可比保持质量/错误提示，不显示未匹配；旧artifact/uncalibrated、白底/见证/视频展示不变。沿用现有布局和REVIEW徽标，无视觉重设计。

## 验收与发布

测试先RED再GREEN：0.5499999/0.55/0.5500001、合法0/1/负分、配置和分数非有限、bool/字符串、不可比/系统失败优先级、状态不可伪造、DTO/HTTP矛盾拒绝、旧签名和uncalibrated/视频完整回归；请求字段和响应字段集合不变。全量pytest/Node、真实模型同图/不同已授权图片+质量负例，记录技术结果而非准确率。测试台实际上传、结果/阈值显示、重置，6条v2及原白底反馈图、presence现场三对回归。

新发布基线固定4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a/API2.3.1。37旧API/console完整ID以fresh audit核对；运行配置SHA基线6d300c20cac3158ce1c5a720eb92f6a9231e3915e2a81e90bfe2653226cf7b5d。
只将image_verification.mode改configured并添加threshold=.55，保留两profile和全部其他配置，尤其presence.5/原ID、portraitface_archive/MODNet、资源/deadline/端口/legacy密钥。严格全量YAML校验只允许该差异，保存原配置及旧4e6a容器用于回滚2.3.1；18001与更早备份不动。签名/密钥/用户媒体/构建产物不入Git。
候选回环18012验收后切换18002/18003，失败恢复原容器；Git固定commit/archivehash，更新八个当前入口、接口例子和新报告，历史报告保持。公司分支/提交规范不变，无main合并/强推/PR/标签。
