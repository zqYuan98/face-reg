# TASK-2026-001 — Presence 0.5 试用阈值运行配置记录

日期：2026-09-28。用户明确指定视频目标出现阈值为 `0.5`，已应用到服务器 37 的 `18002`。
本次仅修改运行配置并停止/启动同一个 API 容器，不重建镜像、不修改算法或模型。
API 仍为 `2.2.0`，代码提交仍为 `d78e723b8fb8737b09e12859e57aa9856a640db6`。

## 当前配置与边界

```yaml
video_presence:
  mode: configured
  threshold: 0.5
  threshold_id: TASK-2026-001-presence-trial-050-20260928
```

这里只展示变更字段，其他 presence 字段及所有非 presence 配置均保持原值。
仓库通用默认值仍为 `uncalibrated / null / null`；此次选择只适用于 37 当前部署，不能自动推广到其他现场。
该值是用户选择的试用配置，不是标定结果，返回 `CONFIGURED_UNVALIDATED`，业务建议仍不自动 PASS。
不修改 images、quick、full 的既有判定语义，不增加活体检测或签名校准门槛。

`score >= 0.5` 仅在扫描计划完成、成功且有可比人脸时构成 FOUND。
无可比脸、无效参考或未完成扫描仍为 INCONCLUSIVE；其他不完整候选条件按现有三态规则处理。
低于阈值的 NOT_FOUND_IN_SCAN 仅表示本次扫描未匹配，并不证明目标从未出现。

此前 100 组无阈值测试中的 96 组有分数结果均大于 0.747，因此代入 0.5 后都会达到分数门槛；
本次未重跑全部 100 组，更不将该推算当成正确率。仍需人工出现标签和独立评估，见
[标签与阈值验收](../PRESENCE_LABEL_VALIDATION.md)。

## 可追溯性与恢复

- 当前配置：`/home/<user>/deployments/face-algorithm-task-2026-001/runtime/algorithm-d78e723b8fb8737b09e12859e57aa9856a640db6.yaml`。
- 原配置 SHA256：`2f69de73fe9fbfc7851d981855673fed24ca842d422555f5d6ec392ee05c593c`。
- 新配置 SHA256：`c0d27189eb153cedb0b4d81890b18e2b99a88f5cedec0ba2a63785a882be0287`。
- 配置备份：同目录 `algorithm-d78e723b8fb8737b09e12859e57aa9856a640db6-before-trial-050.yaml`。
- 原发布状态备份：发布根目录 `presence-release-d78e723b8fb8737b09e12859e57aa9856a640db6-before-trial-050.json`。
- 配置变更记录：发布根目录 `presence-threshold-050-20260928.json`。
- 发布状态保留原 `stage=promoted`、代码、归档和回滚容器 ID，仅更新当前配置哈希并引用上述记录。
- API 容器 ID 仍为 `cf671e39ce84b74ecbd4acd0679e76e5090070ea67daba006fb647d77edb0c76`。
- 新策略指纹：`9f6137e6298f06a8b1e705842f26b9daa06e5dcf227fd37adb68fcf04375cf9c`。
  指纹变化来自配置变化，模型和特征契约未变。

变更前核对容器归属、挂载路径和原配置哈希；容器内验证新配置完整合法且仅三字段变化。
备份原始字节后停止 API，就地保留文件 inode 写入配置，再启动并验证挂载内外哈希一致。
失败路径会恢复原配置和发布状态；正常完成后保留备份。

若只需要撤销本次阈值设置，运维在服务器 37 执行：

```bash
python3 /home/<user>/deployments/face-algorithm-task-2026-001/set-presence-threshold-050-20260928.py restore
```

该命令核对当前配置哈希、容器 ID 和备份后恢复 **2.2.0 无阈值状态**，只重启 API。
不要与 [整版发布回滚](2026-09-28-presence-best-effort-release.md#回滚) 混淆；后者恢复旧 2.1.0。
旧版 2.1.0 及更早回滚容器/配置均保留，18001 与测试台未被重启或修改。

## 实际验证

本次真实局域网 API 测试均为 HTTP 200，响应通过严格 VideoPresenceResult 校验：

| 匿名样本 | 结果 | 分数 | 业务建议 |
|---|---|---|---|
| pair-001 | COMPARED / FOUND / true；15 个候选成功比对 | 0.8814782442 | REVIEW |
| pair-020 | NO_COMPARABLE_FACE / INCONCLUSIVE / null | null | REVIEW |
| pair-046 | INVALID_REFERENCE / INCONCLUSIVE / null | null | RECAPTURE |

三组均返回 threshold=0.5、上述 threshold_id、CONFIGURED_UNVALIDATED 和 liveness_evaluated=false。
不再返回 THRESHOLD_NOT_CONFIGURED；正常命中不等于人工身份真值或业务自动通过。
配置及三态响应相关本地回归：`74 passed`。

真实浏览器经 Playwright 上传同一测试照片/视频，页面显示“本次扫描检测到匹配”、FOUND/true、
分数 0.8815、试用阈值 0.5000、试用阈值 ID 和人工 REVIEW；明确标注精度未验证。
显示 15 次候选比对、212 个解码帧与 5 条证据；浏览器控制台 0 error / 0 warning。
测试后已重置素材和结果并关闭验收浏览器。

最终复查 API/测试台均 healthy、8/8 模型就绪，临时文件数 0；MODNet 保持启用。
API 于 `2026-09-28T07:17:10.879440925Z` 启动，Docker 自动重启计数仍为 0（本次为手工停止/启动）。
18001 和测试台容器的 ID、镜像、启动时间、重启计数及运行配置与变更前一致。
