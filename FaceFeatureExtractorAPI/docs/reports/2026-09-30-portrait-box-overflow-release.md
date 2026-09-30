# TASK-2026-001 — 白底归档检测框越界修正 2.3.1

日期：2026-09-30。状态：候选验收通过后，已于北京时间 14:22 正式切换服务器 37 的
18002/18003 至 API 2.3.1；两容器 healthy，原 2557 容器停止保留用于回滚，18001 未操作。

## 问题与修正边界

授权考勤原图 717×720 在原 API 2.3.0 返回 `FACE_CROPPED`，算法约 38ms 即拒绝。
相同模型单脸框为 (58.4503, 31.3758, 610.5590, 737.0614)，底边超出图像约 17.06px，
但目标选择和五点几何通过，五点均在图内。旧规则把任何模型框外扩当作硬失败，发生在抠图之前；
不能据此证明真实五官缺失。

2.3.1 仅在 `face_archive` 白底生成中允许有限检测框外扩：各侧溢出不超过原框对应宽/高的 5%，
裁至图内的可见面积至少为原框的 90%，两边界均包含等号。还必须通过原目标选择、五点几何、
五点位于实际像素范围、裁后宽高至少 112×112、对齐/推理和原蒙版质量检查。
后续沿用图内框和真实源像素，成功带既有 `PORTRAIT_SOURCE_LIMITED`，不补画缺失头发/肩部。
5%/90% 是固定工程容错边界，不是身份精度标定阈值。严重越界、坏关键点、无脸/歧义和坏蒙版仍失败无图。

`analyze_quality` / `analyze_trusted`、严格 `head_shoulders`、其余五个 v2 接口不变；白底不提取 embedding。
保留 `POST /api/v2/portrait/generate`、multipart `image`、18 个响应字段、嵌套字段、原因枚举、
480×640 JPEG、1.5 倍放大上限和 30 秒预算。整份实际 OpenAPI 与部署前比较仅 `info.version` 变化。
调用方用 `success=true && image_base64` 判断出图；`REVIEW` 可以是成功有图，不能仅接受 `PASS`。

## 升级前基线与本地证据

- 原正式代码：`2557e1707eb77b7cf7a077541030d7ec64f5ae84` / API 2.3.0，18002/18003 healthy。
- 原 API 容器：`9eb1ce46e1f6523e2a022c772016be3aff3857b9e99f9f11fb1d384fad89d622`。
- 原测试台容器：`a4d07dc28b2af9b1e05e416ae0e2205d36c9c605472e24491f733c1c2fc8482a`。
- 原配置 SHA256：`6d300c20cac3158ce1c5a720eb92f6a9231e3915e2a81e90bfe2653226cf7b5d`。
  本次二进制原样复制，SHA 不变，没有配置迁移或回写仓库默认值。
- 修改前相关基线 448 passed / 1 skipped。算法新增用例先得到 28 failed，JPEG/版本整合先得到 3 failed；
  修正后完整 `pytest -q tests tools/visual-console/tests` 为 **1749 passed / 1 skipped**，127.64 秒。
  10 个提示来自既有依赖弃用；跳过项为 Windows 上的 Linux TIME_WAIT 专项。
- Node 测试台测试 **33 passed**。算法相关组合 593 passed、部署 160 passed / 1 skipped；
  这些子集包含在完整结果内，不累加计数。独立需求审查、代码质量及发布保护审查均 Approved。
- 真模型本地原图生成 `success=true / REVIEW / PORTRAIT_SOURCE_LIMITED`、480×640 JPEG，
  算法约 468ms；recognizer 保持 idle。人工检查未新增脸部裁切，源图本身的平边保留，不宣称正式证件照合规。
- 严重越界、恰 5%/90% 边界、关键点越界、裁后小脸、坏蒙版、strict 不放宽等由确定性测试覆盖，
  不用模型对现场图的猜测作为负例真值。

## 固定发布身份

- 公司工作分支：`feat/TASK-2026-001-modular-face-algorithm-api`，无强推、main 合并、PR 或标签。
- 固定运行代码：`4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a`，已正常推送。
- Git archive SHA256：`b8549e697d1d5948e8fbd4afb075269896f893b4524a0e281618e7ce17cfb465`，本地与服务器一致。
- 新运行配置路径：`/home/<user>/deployments/face-algorithm-task-2026-001/runtime/algorithm-4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a.yaml`。
  SHA256 与原配置相同，模板仍为 `face_archive / face-archive-480x640-v1`，MODNet 仍启用。
- API 镜像：`face-algorithm:task-2026-001-4e6a3372ddeb`，
  ID `sha256:a9a7ed7604087951474a74021cf72e677a2503894ccb5b4e597bc608a1e6403d`。
- 正式 API 容器：`1cf05a422d89290099d03b2bead2cd9396e0451e7ca26beef68fba1bf23f150e`，
  启动 `2026-09-30T06:22:02.25472827Z`，重启计数 0。
- 测试台镜像：`face-algorithm-visual-console:task-2026-001-4e6a3372ddeb`，
  ID `sha256:9bf881b5b35573b86a499d11d39c200b1c7c46ccf9e6b0e5a585b883e6899a2f`。
- 正式测试台容器：`e4af1efbeae7b5d189e00838f48b2a9b39aa320882f075e508d7b6121eef8185`，
  启动 `2026-09-30T06:22:11.067265703Z`，重启计数 0。
- 候选仅绑定 `127.0.0.1:18012`；原图 HTTP 200，算法约 1297ms，成功有图且只有 SOURCE_LIMITED。
  验收后候选临时容器由发布流程移除，原生产 API/测试台停止并保留，没有删除用户素材或旧镜像/配置。
  最终 8 模型 ready，五个内部能力（对应六个 v2 接口）全部 ready，临时文件 0。

## 候选与正式验收

候选固定 seed=20260930 的 20 张随机图片、6 个针对输入全部 HTTP 200，无超时，最长 17.297 秒。
26 份上传 JPEG 的哈希与 2.3.0 最终候选输入完全相同；固定样本结果也保持一致：

| 输入组 | 2.3.0 出图 | 2.3.1 出图 | 未生成原因 |
|---|---:|---:|---|
| 随机 20 张 | 17 / 20 | 17 / 20 | 3 张 NO_FACE |
| 5 个针对性可用人像 | 5 / 5 | 5 / 5 | 无 |
| 空白负例 | 0 / 1 | 0 / 1 | NO_FACE |
| 此次反馈原图（另测） | 0 / 1 | 1 / 1 | 原 FACE_CROPPED 已修正，成功带 SOURCE_LIMITED |

26 张固定集共生成 22 张，其中 18 PASS、4 REVIEW（3 SOURCE_LIMITED、1 UPSCALE_LIMITED），
全为可解码 480×640 JPEG，放大不超过 1.5 倍。另测原图为成功 REVIEW，不与固定集混算。
六条 v2 路由冒烟通过，presence 的 MP4/AVI/MOV 三种合成容器均 FOUND + REVIEW；
images/quick/full 保持其原未标定语义，未把 REVIEW 改成身份 PASS。

正式局域网接口再次验证原图 HTTP 200，`success=true / REVIEW / PORTRAIT_SOURCE_LIMITED`，
算法约 1169ms；JPEG 与候选构图一致。又重跑 6 个针对输入和六接口/三容器冒烟，全部 HTTP 200。
整份 OpenAPI 再与原 2.3.0 比较，仅版本号不同；没有字段、嵌套类型、枚举或 HTTP 契约漂移。
授权现场媒体三个匿名回归对保持先前结果，不在本次重新推断身份真值：

| 输入 | 比较状态 / 结果 | 分数 | 建议 |
|---|---|---:|---|
| pair-001 | COMPARED / FOUND / true；15 次候选比对 | 0.8814782442 | REVIEW |
| pair-020 | NO_COMPARABLE_FACE / INCONCLUSIVE / null | null | REVIEW |
| pair-046 | INVALID_REFERENCE / INCONCLUSIVE / null | null | RECAPTURE |

三组 threshold=0.5、原 threshold_id、CONFIGURED_UNVALIDATED、liveness_evaluated=false 不变；
策略指纹仍为 `9f6137e6298f06a8b1e705842f26b9daa06e5dcf227fd37adb68fcf04375cf9c`。

正式测试台用原图执行实际上传、生成、预览及下载，显示“已生成，存在质量提醒”，
浏览器往返 1.34 秒。下载 `portrait-result.jpg` 实际成功，480×640 JPEG，其 SHA256 与
直接正式 API 生成结果一致。应用控制台 0 error / 0 warning；截图人工确认预览与提醒正常。
之后重置为待上传状态（无预览/下载、运行按钮禁用），关闭本次浏览器和 SSH 隧道。
最终正式两容器 healthy、重启数 0、tmp 文件 0；18001 的 ID/镜像/启动时间/重启数与基线一致。

匿名逐例数据和授权媒体仅留工作区忽略目录 `.analysis/portrait-crop-feedback-20260930/`，不进入 Git。
候选结果为 `candidate/results.json`，正式针对输入为 `production/results.json`，原图为
`production-result.json` / `production-output.jpg`，视频为 `production-presence.json`。
浏览器截图留在 API 工作目录的忽略路径 `output/playwright/overflow-production-20260930.png`。
8 个当前入口文档及本报告已独立复核通过，54 个相对文件链接有效；源码和文档按公司规范同步同一工作分支，
历史报告保留，未合并 main、创建 PR 或标签，保留本地工作区与分支供后续开发。

## 不变项与已知限制

presence 仍为 configured、threshold=0.5、threshold_id=`TASK-2026-001-presence-trial-050-20260928`，
`CONFIGURED_UNVALIDATED`、`liveness_evaluated=false`，不新增活体或签名门禁。
API 保持 4 CPU / 6 GiB / 512 PID，测试台 0.5 CPU / 256 MiB / 128 PID，网络、安全和 deadline 不变。
18001 受保护容器 `d6773816222a2200ee76b0b23f35af51786cc64a46147e04933ff7d3281ece7f` 不操作。

之前发布记录中已出现大图在共享服务器负载下超出 30 秒的情况，本修正没有解决或承诺消除性能风险。
超时不是无脸或身份不符；底层任务可能继续占用槽位，不应立即并发重试。
出图统计仅描述固定测试样本，不是识别准确率、生产全量通过率或延迟保证。

## 回滚

必要时由运维使用本次固定 helper 恢复保留的 2557 原容器和同一份原配置：

```bash
python3 /home/<user>/deployments/face-algorithm-task-2026-001/deploy-portrait-overflow-release-4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a.py \
  --commit 4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a --mode rollback
```

恢复目标为 `-prev-2557e1707eb7` 的原 API/测试台、API 2.3.0、presence 0.5，不是更早的 d78 / API 2.2.0。
helper 核对原容器、配置 SHA 与归属，旧 API 8 个具名模型及能力 ready、测试台可达才记录回滚成功。
本次状态已记录 promoted 及原容器完整 ID；不要用历史 helper 恢复其他版本。
失败回滚路径由测试覆盖，不为演示而再次中断正式服务。
