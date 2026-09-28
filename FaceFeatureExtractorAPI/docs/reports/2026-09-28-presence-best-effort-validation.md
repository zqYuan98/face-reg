# Presence 低质量关键帧比对：本地实现验收

日期：2026-09-28。任务：TASK-2026-001。API 源码版本：2.2.0。

## 结论与边界

批准的 [修订设计](../superpowers/specs/2026-09-28-video-presence-best-effort-design.md)
已实现并完成本地回归：低质量提示不再阻止有效特征比对；窗口内受控补采；局部坏候选
不中断其他候选；服务端显式配置试用阈值后支持 FOUND / NOT_FOUND_IN_SCAN / INCONCLUSIVE；
未配置阈值仍返回比对分数、时间和证据。不要求活体检测或旧签名校准包。

这是功能验收，不是生产识别精度验收。真实目标出现标签全部未知；同名文件和高分都不能
当作真值。没有人工标签和业务风险目标，未选择生产阈值，默认仍为 uncalibrated。
试用 FOUND 仍为业务 REVIEW，不等于自动审批通过。

本轮没有推送、合并 main 或部署服务器。服务器 37 的上次发布仍为 API 2.1.0 / 算法提交
`1e6b57b`；18001 未改动。历史发布记录不能代替本次发布验证。新版本发布需单独完成
候选环境、模型/配置、健康与接口冒烟检查，并保留回滚。

## 自动化验证

环境：Windows，本地 Python venv，实际 ONNX 模型；真实素材批量测试限制 BLAS/OMP 线程为 1。
业务旧图片、quick/full、抠图和虚化管线没有改写。

```powershell
python -m pytest tests/unit tests/api tests/integration tools/visual-console/tests -q
node --test tools/visual-console/tests/core.test.mjs
```

- Python：**1264 passed, 1 skipped, 2 warnings**，145.02 秒。
- Node：**31 passed**。
- 跳过项是仅 Linux 的 TIME_WAIT 检查；两项现有警告为 Starlette/httpx 与 tarfile 未来默认行为，未掩盖失败。
- 规格审查分算法/整合/标签、契约/采样/前端两组独立完成；随后独立质量审查通过。
- 配置三态使用合成夹具验证，不以合成阈值冒充现场标定。

审查中发现并修复、均有先失败后通过的回归测试：

1. 真实裁剪坐标 round 后为空：隔离为候选局部技术失败，不阻断后续正常候选。
2. 末帧仅传输而未分析：不能宣称完整扫描或给出否定判定。
3. 视频 PTS 与容器平均帧周期产生毫秒偏差：按已预分配窗口覆盖范围判断，保留真实末帧和资源硬上限。
4. 测试台更换素材后丢弃旧响应；保留原始 mode 校验；未完成扫描标题不再误写完成。
5. 已审核标签强制 video_id，并核对预测关联和人员/视频跨集合泄漏。
6. OpenAPI 路由级描述同步三态，不再宣称新版本永远仅返回 INCONCLUSIVE。

普通解码/推理错误可保留已取得的部分证据；硬截止及执行器超时沿用 HTTP 504 错误协议，
不保证返回部分证据。未以无限等待换取“必须成功”。

## 真实 100 组最终复跑

输入为用户授权的本地 100 组照片/视频；顺序逐请求、实际模型、实际 multipart API。
所有出现真值保持 unknown，不用文件名推导正例或跨组负例。

| 指标 | 结果 |
|---|---:|
| 请求数 / HTTP 200 / success=true | 100 / 100 / 100 |
| 实际获得比对分数 COMPARED | 96 |
| 采样计划完整 scan_complete=true | 99 |
| 视频无可比人脸 NO_COMPARABLE_FACE | 3 |
| 参考照多脸歧义 INVALID_REFERENCE | 1 |
| 实际执行补采的请求 | 41 |
| 带软质量提示的请求 | 40 |
| VIDEO_SCAN_LIMIT_REACHED | 0 |
| INCONCLUSIVE / found=null | 100 / 100（默认未配置阈值） |
| 最佳分数范围 | 0.7471892721–0.9789635014（不是概率） |
| 请求墙钟中位 / P95 / 最大 | 1.734 / 5.187 / 6.407 秒 |

P95 使用排序后 nearest-rank；部分时间与本机测试/浏览器并行，以上只是本轮功能测试耗时，
不是独占服务器性能或 SLA。完整扫描指完成采样计划，不承诺每一视频帧均比对。

匿名未评分编号：pair-020、pair-022、pair-023 为视频 NO_FACE；pair-046 为参考目标多脸歧义。
没有静默当作“不同人”，也未自行猜选参考人脸。

早期补采实现曾误将 41 条视频判为扫描不完整，修复时间戳条件后重复运行恢复 99 条完整扫描，
最佳分数保持不变；最终结果已包括所有审查修复。

最终逐例 JSON 保留在本地忽略目录 `.analysis/video-presence-20260928/presence-best-effort-local-final.json`，
不纳入 Git；仅含匿名编号和允许公开的 API 字段，无媒体、原始文件名、向量或姓名。
SHA-256：`6a7b26332e69f9e9200fc6ad08c4138c5e36b89ee9fe5bc0a6a6060bcc6564bf`。
策略指纹：`841954178bb582ff52b670726ad3388210d2a80e9926eca3c5d492e71dd023f6`。

使用 `scripts/evaluate_presence_labels.py` 对最终 JSON 做无标签汇总：unknown=100、labelled=0、
scored=96；误报率、漏报率、已标注判定覆盖率均为 null。零条已知误报不能解释成零误报率。

## 浏览器端到端

临时服务仅监听 127.0.0.1:18102/18103；不是线上服务器。测试结束已关闭本次浏览器和临时服务。
本地默认 MODNet 未启用，7/8 模型、4/6 UI 能力可用；本次只测试 presence，未覆盖或更改线上 MODNet 配置。

- 真实参考照与视频成功经测试台代理提交，HTTP 200。
- 页面显示“比对已完成，尚未配置判定阈值”，COMPARED、0.8815 分、15 次候选比对、212 解码帧、完整扫描。
- 最多 5 条候选证据展示帧号/时间/分数/候选编号/质量提示。
- 点击 4.033 秒证据后，当前本地 video.currentTime=4.032866，currentSrc 为 blob URL；未请求远端媒体。
- 浏览器错误与警告均为 0；重置后素材/结果清空。
- 结果卡截图保留本地忽略目录 `output/playwright/presence-best-effort-result.png`，不含人脸媒体，不提交。

## 交接

- [开发联调 API 文档](../TASK-2026-001-API-DEBUGGING.md)：完整响应和配置步骤。
- [人工标签与试用阈值验证](../PRESENCE_LABEL_VALIDATION.md)：匿名格式、独立分集和统计口径。
- 下一阶段分别完成人工出现标注/阈值评估与服务器受控发布；不得把本报告当作两者已经完成。
