# TASK-2026-001 — 白底人脸照 2.3.0 发布记录

日期：2026-09-30。当前状态：候选验收后已受控切换服务器 37 的 18002/18003，API 2.3.0，
正式 API 和测试台均 healthy；旧版容器与配置保留用于回滚，18001 未改动。

## 兼容性

保留 `POST /api/v2/portrait/generate`、multipart `image`、全部响应字段及 480×640 JPEG。
新增 `PORTRAIT_SOURCE_LIMITED` / `PORTRAIT_UPSCALE_LIMITED` 提醒枚举；成功有图可为 REVIEW，
业务端须用 `success=true && image_base64` 判断出图，不应仅接受 PASS。
默认 `face_archive` 不强制完整头顶、颈部或肩部，仅使用真实输入像素；有效脸部几何和抠图检查保留。
旧 YAML 缺 mode 仍为严格 `head_shoulders`，本次部署显式迁移 mode/version。

## 已核对的升级前基线

升级前线上 2.2.0，发布代码 `d78e723b8fb8737b09e12859e57aa9856a640db6`；当时 18002/18003 healthy。
运行配置 SHA256 `c0d27189eb153cedb0b4d81890b18e2b99a88f5cedec0ba2a63785a882be0287`。
presence 为 configured、threshold=0.5、threshold_id=TASK-2026-001-presence-trial-050-20260928；MODNet enabled=true。
本轮只迁移 portrait.mode/version，保护所有其他配置、旧容器和 18001。

本地修改前基线：1266 passed / 1 skipped；无既有测试失败。
真实旧版已复现：参考照片和无肩特写 SHOULDERS_NOT_VISIBLE、局部头顶 SUBJECT_CROPPED、小脸 FACE_TOO_SMALL。
真实媒体、派生图片和逐例报告仅留工作区忽略目录，不进入 Git。

## 本地验收

- 完整 `pytest -q tests tools/visual-console/tests`：1598 passed / 1 skipped，122.97 秒；仅 4 个既有依赖弃用提示。
- Node 测试：33 passed。API 新测试固定原 image 请求和全部响应字段，成功 REVIEW JPEG 可解码。
- 唯一跳过为 Windows 下 Linux TIME_WAIT 测试；已在 37 临时回环端口独立验证 TIME_WAIT 可复用、活动监听仍受保护。
- 算法与 API/测试台已交叉需求及代码质量审查；发布脚本补齐旧 2.2 模型就绪检查后才记录回滚成功。
- 旧版 seed=20260930 随机 20 张中出图 12 张；其余 NO_FACE 3、SUBJECT_CROPPED 1、PORTRAIT_GEOMETRY_INVALID 4。
  另 6 个针对输入：5 个可用人像因肩部/头顶/模板小脸限制失败，1 个空白 NO_FACE。

## 最终候选真实图片验收

最终代码 `2557e1707eb77b7cf7a077541030d7ec64f5ae84` 在独立回环候选端口复测相同输入，
26 张均返回 HTTP 200，本轮未发生超时，最长 26.797 秒。

| 输入组 | 旧版出图 | 新版出图 | 未生成原因 |
|---|---:|---:|---|
| 固定随机 20 张 | 12 / 20 | 17 / 20 | 3 张 NO_FACE，与旧版相同 |
| 5 个针对性可用输入 | 0 / 5 | 5 / 5 | 无 |
| 空白负例 | 0 / 1 | 0 / 1 | NO_FACE |

合计出图 22 / 26；其中 4 张成功 REVIEW（3 张 SOURCE_LIMITED、1 张 UPSCALE_LIMITED），
其余 18 张 PASS。全部派生 JPEG 可解码为 480×640，放大不超过 1.5 倍。
人工查看无肩特写、小脸、源头顶受限和随机输出：使用真实像素换白底，未补画缺失肩部或头发；
源图裁切的平边可能保留，白边可增大，不宣称达到正式证件照构图。
本统计仅为此固定样本的技术出图结果，不是身份识别精度、生产全量通过率或延迟保证。

逐例证据位于本地忽略目录 `.analysis/portrait-release-20260930/candidate-complete/records-progress.json`
及 `http-cases.jsonl`。早期批次的超时记录保留，不以最终成功复测覆盖历史失败。

## 候选问题与复测记录

- 首个候选 `1199a99` 发现可靠头顶触及源边缘但额头留白充足时漏报提醒。补丁 `2557e17`
  只调整 archive 分支，严格模式与稀疏细发例外保持不变；3 个新测试先失败，portrait 目录
  225 项通过，独立交叉审查通过后提交公司工作分支。
- 早期大图批次出现 30 秒 `HTTP 504 / ALGORITHM_TIMEOUT`；同一时段旧正式版本对相同
  12.58 MP 输入也出现 30 秒超时，旧版 15.93 MP 输入从此前 11.94 秒变为 24.20 秒。
  新旧运行资源均为 4 CPU / 6 GiB，线程环境完全相同；无 OOM，候选可见内存压力。
  不能据此断言唯一根因，亦不能承诺所有 25 MP 输入在所有负载下均能 30 秒完成。
  本次未放宽截止时间、资源、尺寸上限或质量硬检查；超时不是没有人脸，也不是身份不符。
  底层超时任务可能继续占用执行槽，验收遇超时后留出结束时间，不立即并发重试。
- 浏览器已验证小脸产生 `success=true / REVIEW / PORTRAIT_UPSCALE_LIMITED` 时显示
  “已生成，存在质量提醒”，预览可见，实际下载 `portrait-result.jpg` 成功。
- 旧生产与新候选整份 OpenAPI 对照只有四处变化：版本、portrait summary、portrait description、
  ReasonCode 新增两枚举。六个 POST 接口的所有请求/响应字段、类型、必填/可空、嵌套结构和
  HTTP 状态码完全一致。严格枚举客户端需支持新原因码，调用方按 success 与图片判断出图。

## 发布身份与配置

- 固定运行代码：`2557e1707eb77b7cf7a077541030d7ec64f5ae84`，公司工作分支已正常推送。
- Git archive SHA256：`0dbeb905a34e81d10fc8eb7333d697e12ab3a526e46ad4b1b7fe47ead54ff4a9`，本地与服务器一致。
- 新运行配置 SHA256：`6d300c20cac3158ce1c5a720eb92f6a9231e3915e2a81e90bfe2653226cf7b5d`。
  严格验证仅 portrait mode/version 变为 face_archive / face-archive-480x640-v1；所有其他数据与原配置一致。
- API 镜像 `face-algorithm:task-2026-001-2557e1707eb7`，镜像 ID
  `sha256:bb96cee4f0f1cd543e5961dc84b18e3d3afe8442f2e84ecc910eb2e191696f6d`。
- API 容器 `9eb1ce46e1f6523e2a022c772016be3aff3857b9e99f9f11fb1d384fad89d622`，
  启动 `2026-09-30T01:58:28.008398711Z`（北京时间 09:58），重启计数 0。
- 测试台镜像 `face-algorithm-visual-console:task-2026-001-2557e1707eb7`，镜像 ID
  `sha256:de4a4c9c60eb4d8b21b8f43ce19c3f30df308487e20cbbe071132d16b9da923f`。
- 测试台容器 `a4d07dc28b2af9b1e05e416ae0e2205d36c9c605472e24491f733c1c2fc8482a`，
  启动 `2026-09-30T01:58:36.846518882Z`，重启计数 0。
- 模型 8/8 就绪、五个内部能力（对应六个 v2 接口）全部 ready；MODNet 保持启用。
  API 仍为 4 CPU / 6 GiB，测试台 0.5 CPU / 256 MiB，保持只读文件系统和原安全/网络参数。
- 受保护 18001 容器 `d6773816222a2200ee76b0b23f35af51786cc64a46147e04933ff7d3281ece7f`
  的 ID、镜像、`2026-09-15T09:43:38.483333895Z` 启动时间、重启计数 0 均与升级前一致。
- 最终候选临时容器已按发布流程移除；初次 1199 候选仅停止保留，不对外监听。
  没有删除用户素材或旧生产镜像、配置、回滚容器。

## 正式环境回归

局域网 `http://<server-37>:18002` 重新验证 6 个针对性白底输入，以及 witness、images、
video/quick、video/full、video/presence。全部 HTTP 200；白底及 presence 响应通过严格 DTO 校验，
其余接口核对关键返回语义，完整契约另由本地 API 回归覆盖。
presence 对 MP4、AVI、MOV 合成容器均返回 FOUND + REVIEW；旧 images/quick/full 的未标定边界不变。
另用授权现场媒体复测原来的三个匿名回归对，不重新推断身份真值：

| 输入 | 比较状态 / 结果 | 分数 | 建议 |
|---|---|---:|---|
| pair-001 | COMPARED / FOUND / true，15 次成功候选比对 | 0.8814782442 | REVIEW |
| pair-020 | NO_COMPARABLE_FACE / INCONCLUSIVE / null | null | REVIEW |
| pair-046 | INVALID_REFERENCE / INCONCLUSIVE / null | null | RECAPTURE |

三组保持 threshold=0.5、原 threshold_id、CONFIGURED_UNVALIDATED、liveness_evaluated=false；
策略指纹仍为 `9f6137e6298f06a8b1e705842f26b9daa06e5dcf227fd37adb68fcf04375cf9c`。
没有增加活体检测或签名 calibration 门槛，也不把用户试用阈值当成生产精度标定。

测试工具曾误用 `model_validate(dict)` 验证 presence 严格模型，导致 Python 枚举/元组类型报错；
改为网络 JSON 对应的 `model_validate_json` 后重跑六接口通过，没有因此修改服务 DTO 或放宽校验。

正式测试台浏览器重新上传小脸输入，1.22 秒返回“已生成，存在质量提醒”，480×640 JPEG 预览及
实际下载成功；控制台 0 error / 0 warning。测试后重置会话素材并关闭验收浏览器，候选的本地
代理和 SSH 隧道也已关闭。最后复核正式两容器 healthy、临时文件 0、18001 未变。
所有源码和当前联调文档按公司提交规范同步同一工作分支，未合并 main、创建 PR 或版本标签。

## 回滚

必要时运维在服务器 37 执行本次固定 helper：

```bash
python3 /home/<user>/deployments/face-algorithm-task-2026-001/deploy-portrait-release-2557e1707eb77b7cf7a077541030d7ec64f5ae84.py \
  --commit 2557e1707eb77b7cf7a077541030d7ec64f5ae84 --mode rollback
```

该命令核对归属与配置后，恢复名称后缀 `-prev-d78e723b8fb8` 的原 API/测试台容器及原配置，
恢复 API 2.2.0 + 用户试用阈值 0.5；不动 18001。只有旧 API 模型与能力重新就绪、测试台可达
才记录回滚成功。本轮没有为了演示回滚再中断正式服务，失败路径由回归测试验证。
不要执行旧 2026-09-28 发布脚本的 rollback，那是另一历史版本的恢复目标。
