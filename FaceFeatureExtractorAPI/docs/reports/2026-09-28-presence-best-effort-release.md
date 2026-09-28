# TASK-2026-001 — Presence 2.2.0 发布验收

日期：2026-09-28。当前状态：2.2.0 已切换到 37 正式端口，局域网接口验收通过。

## 固定版本与范围

- 应用/发布提交：`d78e723b8fb8737b09e12859e57aa9856a640db6`，包含算法实现 `4d3a761`。
- API 版本：`2.2.0`；公司分支：`feat/TASK-2026-001-modular-face-algorithm-api`。
- 公司 Git 已推送固定提交；无强推、main 合并或自动 PR。
- 仅发布 37 的 `<server-37>:18002` API、`<server-37>:18003` 测试台。
- `18001` 保留服务不参与构建、切换、回滚或清理。
- 默认 presence 为 `uncalibrated`：仍返回数值比对和关键帧证据，身份结论保持 `INCONCLUSIVE/null`。
  没有新增活体门槛或签名校准包门槛；没有擅自选择现场判定阈值。

本报告证明功能发布流程，不证明生产识别精度。文件同名只代表业务关联，不能作为目标在视频中出现的真值。
试用阈值支持不等于已通过现场标定，具体要求见 [匿名标签与阈值验收](../PRESENCE_LABEL_VALIDATION.md)。

## 发布前检查与工具修复

使用原有 SSH 身份及严格主机密钥检查接入 yzq；不修改 authorized_keys、SSH 配置、防火墙或用户权限。
server-fleet-kit 当前仅含个人资产，未用于操作公司服务器；公司发布使用本项目既有、审查过的发布工具。

已核对旧正式 API 2.1.0 / `1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b`，两容器均 healthy、RestartCount=0；
8 个模型全部就绪。旧配置 SHA256：`994f8c49a3cad6a4e6a9fae4b47d42e40b62ae9ffc5e8666f09df2893edf4a8d`。
发布前磁盘可用 88.02 GiB，可用内存约 123 GiB。

发布工具原先固定更早的 `872c969` 基线；本次按测试驱动修复：

- 使用当前完整基线提交对应源码/配置和 12 位镜像标签；更早备份保留。
- 已有 presence 配置仅补缺少的 threshold、threshold_id、max_supplemental_frames，保留原值。
- 非 presence 配置经容器内 YAML 比较必须完全相等，MODNet 继续启用；拒绝不匹配策略而非覆盖。
- 健康守卫同时验证 2.2.0、实际响应 DTO、新字段、试用枚举和证据字段；回滚只要求旧版活性。
- 独立审查通过。部署/OpenAPI 相关 73 passed / 1 skipped；完整回归 **1294 passed / 1 skipped**，Node **31 passed**。
  跳过项为 Windows 上的 Linux TIME_WAIT 用例，已在 37 随机回环端口复验释放端口及活跃监听保护。

## 构建及配置

归档仅来自固定 Git 提交，不含 .env、私钥、用户测试媒体、本地虚拟环境或构建目录。
通过已有离线 wheelhouse 和缓存构建 API/测试台；requirements 与基线一致，模型逐项校验 manifest。

| 项目 | 值 |
|---|---|
| 源码归档 SHA256 | `8defbade5be6e1535f5d485851546c8080d1cfca050c9d314cc4423de49a730c` |
| 发布工具 SHA256 | `9548b346878afaa9aab816830ac3ee4e6e4aac63fa57b3d9edfe53bcc7a70ea1` |
| 新运行配置 SHA256 | `2f69de73fe9fbfc7851d981855673fed24ca842d422555f5d6ec392ee05c593c` |
| API 镜像标签 | `face-algorithm:task-2026-001-d78e723b8fb8` |
| API 镜像 ID | `sha256:30604c2287696434386aa3887b4d47c1aca1fcf4f1de92b18aae81e9dad0289c` |
| 测试台镜像标签 | `face-algorithm-visual-console:task-2026-001-d78e723b8fb8` |
| 测试台镜像 ID | `sha256:23fd12ff52e6a325c514570731f125353b175b8925d950920d9efe096aca07b1` |

API 保持 4 CPU / 6 GiB / 512 PID / 2 GiB 临时 tmpfs；测试台 0.5 CPU / 256 MiB / 128 PID。
只读根目录、ALL cap-drop、no-new-privileges、非 root 用户、日志轮转均保留。

## 候选验证

候选仅绑定 `127.0.0.1:18012`，使用受控临时回环转发供本机测试。8/8 模型、全部能力就绪，
OpenAPI 2.2.0、新 DTO 字段和 6 条 v2 路由验证通过。旧正式 18002/18003 在候选期间继续服务。

- 六接口合成样本冒烟通过；白底照/虚化返回派生图。
- presence 对 MP4/MPEG-4、AVI/MJPEG、MOV/MPEG-4 均成功比对且扫描完整；不宣称支持所有同扩展名编码。
- 旧图片/quick 保留未标定 REVIEW；短合成 full 仍为 INSUFFICIENT_VALID_FRAMES/RECAPTURE。
- legacy 检测 200，特征导出 404，presence 缺必填文件 400；presence 不再出现 CALIBRATION_REQUIRED。
- 每个成功 presence 响应通过本地严格 VideoPresenceResult DTO 验证。
- 100 组真实素材在候选容器（与正式完全相同的镜像及配置）上串行完成：HTTP 200 / success=true 均 100 组，99 组完整扫描，96 组返回数值分数。
- pair-020 / 022 / 023 为 NO_COMPARABLE_FACE；pair-046 为 INVALID_REFERENCE / MULTIPLE_FACES。
  99 组建议 REVIEW，1 组 RECAPTURE。41 组实际触发补采样，40 组有质量提示，无 VIDEO_SCAN_LIMIT_REACHED。
- 默认未配置阈值，因此 100 组均为 INCONCLUSIVE / found=null；这是判定策略，并非比对未执行。
- 96 组分数范围 0.7471892721–0.9798032809；串行墙钟耗时中位数 6.883 秒、P95（nearest-rank）26.687 秒、最大 32.203 秒。
  这是共享服务器 4 CPU 限额下的功能测量，不是并发基准或 SLA。
- 与本地回归的可比对数量和异常分组一致；16 组最高分相差超过 1e-6，最大绝对差 0.0195258914。
  不承诺跨平台浮点分数完全相等，也不在本次发布中推断差异原因。
- 算法指纹全部为 `841954178bb582ff52b670726ad3388210d2a80e9926eca3c5d492e71dd023f6`，与本地一致。
- 本地匿名汇总文件 `presence-best-effort-candidate-d78e723.json` 的 SHA256：
  `4b558f8f3d1bb411f0b7169da17f7a9f9f536535a3ca51843e25615151389651`。
  原始媒体、原文件名和逐条人脸结果不提交 Git。没有人工真值，不能把 96 组有分数解释为 96% 准确率。

## 正式切换与最终验证

正式切换完成于 2026-09-28 14:42（Asia/Shanghai），两容器 healthy、RestartCount=0：

| 服务 | 容器 ID | 启动时间（UTC） |
|---|---|---|
| API 18002 | `cf671e39ce84b74ecbd4acd0679e76e5090070ea67daba006fb647d77edb0c76` | `2026-09-28T06:42:35.784393272Z` |
| 测试台 18003 | `65eaacd8f838d0f02c2a271ed0969321ceb8601391d8ccd24e4b11de240872a5` | `2026-09-28T06:42:42.61321842Z` |

仅绑定主机内网地址 `<server-37>`，未改为 0.0.0.0。发布状态文件 stage=promoted，已保存上一版 original IDs。
正式局域网 API 的六接口烟测、8/8 模型健康、OpenAPI 2.2.0、严格 DTO 与三种视频容器样本均验证通过。
MP4/MOV 合成样本最高分 0.9886403539，AVI 为 0.9964060410；均完整扫描，不使用这些合成分数标定阈值。

测试台 index.html / core.js / app.js / styles.css 的 HTTP 字节均与固定发布归档完全相等，Cache-Control 为 no-store。
Windows 归档换行是 CRLF，归一化后与固定 Git 提交的 LF blob 相等；未把换行差异误判为旧页面。

真实浏览器经 Playwright 验证正式测试台的上传、运行、显示证据、点击定位及重置完整链路：

- 参考照片 812×816、视频约 7.1 秒；返回 COMPARED，15 个成功比对候选，212 个解码帧，完整采样计划。
- 显示最高分 0.8815、5 条证据、1024 维和未配置阈值；主提示为“比对已完成，尚未配置判定阈值”。
  本次往返 6.27 秒，不把该单例作为性能承诺或人工真值。
- 点击 4.033 秒证据后，本地视频 currentTime=4.032866 秒、duration=7.094144 秒、来源为 blob:。
  候选定位不请求远端媒体，浏览器控制台 0 error / 0 warning。
- 重置后照片、视频与结果均清空，运行按钮恢复禁用；验收浏览器已关闭。
- 只截取无照片/视频内容的结果栏作为本地验收附件，不提交媒体或截图到 Git。

这里的“完整”仅指该采样计划完成，并非每个解码帧都做人脸比对，也不是证明目标从未出现。

切换后额外通过正式局域网接口抽查 pair-001 / 002 / 004 / 020 / 022 / 023 / 046 / 060 / 093 / 100：
10 组均 HTTP 200 / success=true；6 组有分数、9 组完成采样计划，特意包含上述 4 组异常素材，
异常分组和状态与候选结果一致。全部仍为 INCONCLUSIVE/null；本批不作为随机精度样本。
匿名汇总 `presence-best-effort-server-spot-d78e723.json` SHA256：
`21d46bae470a77588d166924064810898d5536f40962ed27c7f05a4159071f2f`。

最终健康复查仍为 API/测试台 healthy、0 重启、模型 8/8 就绪；API 临时目录文件数为 0，MODNet 仍启用。
当时 API 内存约 1.93 GiB/6 GiB、119 PID；测试台约 19.64 MiB/256 MiB、1 PID。
磁盘可用 87.68 GiB；这是单次快照，不替代后续容量监控。

18001 的容器 ID `d6773816222a2200ee76b0b23f35af51786cc64a46147e04933ff7d3281ece7f`、镜像、启动时间
`2026-09-15T09:43:38.483333895Z` 和 RestartCount=0 在候选前后及正式切换后均未变化。
候选容器已由发布工具移除，本机临时 SSH 转发已关闭；历史回滚容器、镜像、配置均保留。

## 回滚

本次切换已保留以下两个旧 2.1.0 容器，不删除旧源码、配置或镜像：

- `face-algorithm-task-2026-001-prev-1e6b57b5b1dd`，原 ID `2f58ecdd7ad5579a19a64dba0511ddd8e620d9f652b8da3ef44d567e500cceeb`。
- `face-algorithm-visual-console-task-2026-001-prev-1e6b57b5b1dd`，原 ID `4361d0c565396a398193fea2407e8f81fe13f08ab612c9cb37721bddd1cb48aa`。

本次切换已保存 original IDs。需要恢复上一版时执行：

```bash
python3 /home/<user>/deployments/face-algorithm-task-2026-001/deploy-presence-release-d78e723b8fb8737b09e12859e57aa9856a640db6.py \
  --commit d78e723b8fb8737b09e12859e57aa9856a640db6 --mode rollback
```

脚本验证两组容器归属后才移除本次新容器、恢复原 ID；不操作 18001。
不要使用历史发布报告里的旧 rollback 命令，以免回到更早的 872c969。
