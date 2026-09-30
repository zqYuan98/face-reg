# Presence 匿名标签与试用阈值验证

本文方法适用于 2.3.0 沿用的 presence 契约，不因白底人脸照策略变化而改变标签或阈值规则。
2026-09-30 源码处于 2.3.0 构建/候选验证阶段；本次部署状态及回滚入口见
[白底人脸照发布记录](reports/2026-09-30-attendance-face-portrait-release.md)，本文不代表已上线。

升级前基线：2026-09-28 服务器 37 的 `18002/18003` 发布 API `2.2.0`，当时均 healthy，
发布代码为 `d78e723b8fb8737b09e12859e57aa9856a640db6`；该次证据见
[2.2.0 历史发布记录](reports/2026-09-28-presence-best-effort-release.md)。37 在同日后续按用户指定仅将
presence 运行配置设为试用阈值 `0.5`（`configured`），返回 `CONFIGURED_UNVALIDATED`，业务仍需人工 REVIEW；
该次阈值调整未改源码默认无阈值、代码版本、images/quick/full、18001 或测试台。
本次 2.3.0 升级保留 `0.5` 及原阈值 ID。配置依据见
[0.5 试用阈值记录](reports/2026-09-28-presence-threshold-050.md)。
本文提供离线标注/汇总方法；用户指定试用点不代表人工标签已齐、生产阈值已验证或识别精度已验证。
完整响应契约见 [API 联调文档](TASK-2026-001-API-DEBUGGING.md)。

## 1. 先分集，再独立标注

同名照片与视频仅关联同一业务人员，不保证目标实际出现；不同组人员的照片与视频也不能
自动构成负例，因为视频可能有多人。模型的最高分是相似度，不是概率、真值或人工标签。
没有人工确认的照片-视频对保持 `unknown`，不能由文件名、模型预测或选出的关键帧补标签。

1. 在看模型分数和调阈值前，按人员固定 `tuning`（调参）与 `evaluation`（留出评估）集合。
   同一人的照片、对应视频及所有视频帧不得跨集。同一个视频用于多个 reference/pair 时仍须
   使用同一个匿名 `video_id`；不能通过换 `pair_id` 隐藏跨集复用。
2. 多人视频中的全部关联人员须在同一集合；有跨集冲突则合并关联组至同一集合或排除样本。
   `appearing_groups` 如实列出已知关联人员分组，不能为通过检查而省略。工具无法发现未填报的人。
3. 独立审核人员先看完整视频，默认不展示模型分数，给出 `present`、`absent` 或 `uncertain`。
   present 记录出现时间段；absent 必须基于完整视频复核，不能仅看算法抽帧；肉眼无法确认用 uncertain。
   未审核用 unknown。原始审核记录保留标注来源、版本与争议处置，不用模型意见替代人工确认。
4. 只用 tuning 的人工标签及业务风险目标选试用阈值，记录候选阈值的误报、漏报与无法判断权衡。
   evaluation 仅用于最终视频级评估，不能再用于挑阈值。人员/视频划分与阈值冻结后再评估。

无标签时可以继续功能开发和覆盖回归，但不能自称识别准确率或现场判定已验收。所有统计
采用“一张参考照与完整视频的一次扫描”口径，不用逐帧准确率替代视频级结果。

## 2. 匿名 CSV 格式

脚本为 [`scripts/evaluate_presence_labels.py`](../scripts/evaluate_presence_labels.py)。
`--labels` 接受 UTF-8（可带 BOM）CSV，`--predictions` 接受 UTF-8 JSON。
匿名 ID 格式为 `[a-z][a-z0-9-]{0,63}`，例如 `pair-one`，不允许姓名、原始文件名或路径。

| 列 | 规则 |
|---|---|
| `pair_id` | 必填且唯一，连接预测记录；不能有无对应预测的标签 |
| `reference_id` | 建议填写的匿名参考照标识，用于独立审核追溯；当前脚本不校验此列 |
| `video_id` | present/absent/uncertain 必填，明确标识实际同一视频，不回退到 pair_id；预测也有此字段时必须相同；建议 unknown 也填 |
| `identity_group` | 已审核标签必填的匿名参考人员分组；同一组不得跨 tuning/evaluation |
| `appearing_groups` | 同片其他已知人员的匿名组，以英文分号分隔；参与跨集检查 |
| `split` | 已审核标签必填，严格为 tuning 或 evaluation；应事先确定，不依据分数划分 |
| `presence` | present / absent / uncertain / unknown；不把 uncertain/unknown 当负例 |
| `review_version` | present/absent/uncertain 必须有非空审核版本；unknown 可为空 |
| `review_source` | 建议填写匿名审核来源；当前脚本不验证来源真实性 |
| `presence_intervals_seconds` | present 的人工出现时间段，如 `1.2-2.4;5.0-5.8`；当前脚本不解析此列，须由审核流程维护 |

空白模板表头（不含真实人员标签）：

```csv
pair_id,reference_id,video_id,identity_group,appearing_groups,split,presence,review_version,review_source,presence_intervals_seconds
```

仅示意未审核状态的一行 synthetic-only 数据，不是现场标签：

```csv
pair_id,reference_id,video_id,identity_group,appearing_groups,split,presence,review_version,review_source,presence_intervals_seconds
pair-one,reference-one,video-one,person-one,,tuning,unknown,,,
```

工具会拒绝重复 pair_id、孤立标签、不合法标签、已审核标签缺 video_id/split/review_version、
人员/视频跨集以及标签与预测的 video_id 冲突；即使用 `--split`，也先对完整输入进行泄漏检查。
校验通过只说明填写的标识一致，不能证明人工已看完视频或人员关联完整。

## 3. 预测 JSON 输入

输入可以是记录数组，也可以是 `{"records": [...]}`。每条至少包含匿名 `pair_id`、
`decision`、`found`、`best_score`；FOUND/NOT_FOUND_IN_SCAN 还要求 `success=true`、
`scan_complete=true` 和有限分数。建议同时保存实际 `video_id`、版本/策略指纹、阈值/ID、
comparison_status、计数与耗时以供审计。三态必须严格对应 true/false/null，分数须在 [-1,1]。

下面是工具所需的投影记录，不是完整 API DTO；全部为 synthetic-only，无真实媒体、标签或阈值：

```json
{
  "records": [
    {
      "pair_id": "pair-one",
      "video_id": "video-one",
      "success": true,
      "scan_complete": true,
      "decision": "INCONCLUSIVE",
      "found": null,
      "best_score": 0.42
    }
  ]
}
```

必须先用 API DTO 验证响应，再作匿名投影；本工具不是全量 DTO 校验器，不会验证全部阈值、
质量提示或计数，也不重算 threshold 决策。完整扫描但无阈值的有分数结果可以是 INCONCLUSIVE。
configured 使用服务端固定且显式配置的试用阈值，返回 CONFIGURED_UNVALIDATED；没有默认
推荐值，也不接受每次 HTTP 请求任意传阈值。synthetic-only 示例不能当真实部署阈值。

## 4. 运行与统计解释

在 API 项目目录中运行；示例路径指向受控本地目录，真实输入和逐例结果不得提交 Git：

```powershell
# 无标签：只报告请求/评分覆盖，不推断准确率
python scripts/evaluate_presence_labels.py --predictions C:\secure\presence\predictions.json

# 先检查全部标注的人员/视频泄漏，再只汇总预分配的 tuning 集
python scripts/evaluate_presence_labels.py --predictions C:\secure\presence\predictions.json --labels C:\secure\presence\labels.csv --split tuning

# 冻结阈值后，单独报告未参与调参的 evaluation 集
python scripts/evaluate_presence_labels.py --predictions C:\secure\presence\predictions.json --labels C:\secure\presence\labels.csv --split evaluation
```

输出到标准输出，不修改标签或阈值，不上传任何数据。没有对应标签的预测按 unknown 统计。
`--split` 只保留该集合标签对应的预测；不要省略 labels 后用空的 split 报告假装已评估。

| 输出 | 含义 |
|---|---|
| `requests`, `scored` | 记录总数、best_score 非 null 数；可评分不是已判定或正确 |
| `present`, `absent`, `uncertain`, `unknown`, `labelled` | 标签各类数量；labelled 仅 present + absent |
| `labelled_inconclusive`, `present_inconclusive`, `absent_inconclusive` | 已知标签中无法判断的数量，单独报告，不当正确或负例 |
| `false_positives`, `false_negatives` | absent 却 FOUND / present 却 NOT_FOUND_IN_SCAN 的视频级数量 |
| `correct_decisions` | 已知正/负标签中给出正确非空判定的数量，不包含无法判断 |
| `false_positive_rate`, `false_negative_rate` | 误报数 / 全部 absent，漏报数 / 全部 present；分母含该类无法判断记录，应同时阅读覆盖率 |
| `labelled_decision_coverage` | (labelled - labelled_inconclusive) / labelled |

分母为零时相应比例返回 null，不是 0% 错误率。无标签时 known-label 计数可能为零，
不能解释为“零误报/零漏报已验证”。脚本不计算 ROC、置信区间、自动最优阈值、场景分层或耗时分布；
完整验收还需独立报告标签来源/数量、可比较及无法判断比例、暗光/模糊/侧脸/小脸/多人/短暂出现
分层、P50/P95 耗时与运行环境。样本不足就说明不足，不把“可评分比例”写成准确率。

## 5. 保密与完成边界

原媒体、原始文件名映射、人员标签与逐例输出留在本地受控且忽略的目录；不放入公司 Git、
镜像、自动化 fixture 或普通日志，不提交生物特征。仓库只保留工具、空白格式、合成夹具和匿名汇总。

代码功能通过、现场覆盖回归、人工阈值/留出精度验收、受控发布是四类不同证据。
试用 FOUND 仍为业务 REVIEW，不代表活体/反翻拍、合同审批通过或生产精度已验证。
人工标签和风险目标不足时明确写“生产精度未验证”，不据本工具运行成功宣布业务验收。
