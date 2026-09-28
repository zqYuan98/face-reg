# TASK-2026-001 视频目标出现接口发布验收

## 本地证据

2026-09-28，新接口 `POST /api/v2/verify/video/presence`；旧 quick/full 保留持续一致性语义。
首版没有独立标定阈值，所有身份结果为 `INCONCLUSIVE / found=null`，不得视为自动放行。

- 最终完整 `tests/unit tests/api tests/integration tools/visual-console/tests`：1043 passed，
  1 skipped（Windows 跳过 Linux 专用端口测试），2 条告警（既有 Starlette/httpx 弃用及
  Python 3.14 tar 默认过滤行为提醒）；包含实际模型集成测试，119.80 秒。
- Node 测试台：20 passed，包含伪造 ACTIVE/FOUND 状态的失败关闭回归。
- 独立规格及质量审查完成：测试台提前接受未来已标定状态的缺口已修复；后端无阻断项。
- 默认最大时长和采样帧预算同时生效，实际末帧计入 1200 帧预算，已在联调文档明确。

## 100 组现场素材，本地完整 HTTP 链路

仅用匿名编号，未保存/提交原始姓名、媒体、向量或关键点。相同文件名只表示用户提供的配对，
不是经过人工确认的身份真值，以下不是识别准确率或业务通过率。

| 指标 | 结果 |
|---|---:|
| 请求 / HTTP 200 | 100 / 100 |
| 可评分配对 | 96 |
| 完整扫描 | 99 |
| 无可用脸视频 | 3（pair-020、022、023） |
| 参考照多人歧义、未扫描视频 | 1（pair-046） |
| INCONCLUSIVE / found=null | 100 / 100 |
| REVIEW / RECAPTURE | 99 / 1 |
| 最高候选分数范围 | 0.73411175—0.97896350 |
| HTTP 墙钟耗时中位数 / 最大 | 1.180 / 2.157 秒 |

96 组可评分及四组原因与上一轮独立诊断一致。三个无可用脸视频仍完成扫描，但不能据此
自动判为“目标未出现”。出现检测不提供活体、反翻拍或合同有效性结论。

## 服务器发布

2026-09-28 已切换上线，API 版本 2.1.0。算法/测试台镜像基于固定业务提交
`1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b`，发布脚本修正提交 `eee1e57`；后续文档提交
不改变已部署应用代码。公司分支为 `feat/TASK-2026-001-modular-face-algorithm-api`。

| 项目 | 已核对值 |
|---|---|
| API | `http://<server-37>:18002` |
| 测试台 | `http://<server-37>:18003` |
| OpenAPI | `http://<server-37>:18002/openapi.json` |
| API 镜像标签 | `face-algorithm:task-2026-001-1e6b57b5b1dd` |
| API 镜像 ID | `sha256:8f4d357f9b4411dc603145dc268b1396cd68bae865812770dc026aafc2f2b1c5` |
| API 容器 ID | `2f58ecdd7ad5579a19a64dba0511ddd8e620d9f652b8da3ef44d567e500cceeb` |
| 测试台镜像标签 | `face-algorithm-visual-console:task-2026-001-1e6b57b5b1dd` |
| 测试台镜像 ID | `sha256:0b99e3f755cd54676b6299f1c20e9614b6fd92fc45c3351192ba21494e2aec96` |
| 测试台容器 ID | `4361d0c565396a398193fea2407e8f81fe13f08ab612c9cb37721bddd1cb48aa` |
| 源码归档 SHA256 | `128c614b8622c2145fe53804e9044cd15bcfc9b17d40b9f78b990c292a345476` |
| 实际运行配置 SHA256 | `994f8c49a3cad6a4e6a9fae4b47d42e40b62ae9ffc5e8666f09df2893edf4a8d` |

两个端口均只绑定 `<server-37>`，不是 `0.0.0.0`。API 4 CPU / 6 GiB / 512 PID，测试台
0.5 CPU / 256 MiB / 128 PID；只读根目录、ALL cap-drop、no-new-privileges 均保留。
API 临时目录为 2 GiB tmpfs；日志轮转分别 10 MiB×3 / 5 MiB×3。实际配置由旧运行配置
追加 presence 字段，其他 YAML 值经相等校验，MODNet 保持启用；未覆盖为仓库默认禁用值。

### 候选和浏览器验证

- 候选仅绑定 `127.0.0.1:18012`，验证后已移除，临时 SSH 转发也已关闭。
- 8/8 模型、5 个 readiness 能力键、6 条 v2 路由全部就绪；OpenAPI 版本 2.1.0。
- 六接口实际调用通过；白底照与背景虚化返回派生图，图片/视频身份接口不错误放行。
- presence 对 MP4/MPEG-4、AVI/MJPEG、MOV/MPEG-4 三种合成容器编码样本均完成扫描；
  另验证现场 H.264 MP4。该结论不代表支持所有同扩展名编码。
- 短合成视频的旧 full 保持 `INSUFFICIENT_VALID_FRAMES/RECAPTURE`，不把旧语义改成出现检索。
- legacy 检测返回 200，特征导出仍 404，新接口缺必填文件返回 400。
- 浏览器实际选择“视频目标出现”、上传匿名照片+现场视频、提交并收到证据；显示
  `INCONCLUSIVE / null`、最佳分数 0.8815 和 5 条证据，无控制台错误，重置清空预览/结果。
- 正式 LAN 六接口及三种视频容器再次冒烟通过；临时上传最终清理情况见下节。

### 正式 LAN 的 100 组回归

| 指标 | 结果 |
|---|---:|
| 请求 / HTTP 200 | 100 / 100 |
| 可评分 / 完整扫描 | 96 / 99 |
| 无可用脸 / 参考歧义 | 3 / 1（与本地相同匿名编号） |
| INCONCLUSIVE / found=null | 100 / 100 |
| REVIEW / RECAPTURE | 99 / 1 |
| 最高候选分数范围 | 0.73411175—0.97980328 |
| HTTP 墙钟耗时中位数 / 最大 | 5.242 / 13.312 秒 |

这是当前 4 CPU 容器的功能验收，不是吞吐量基准；浏览器冒烟与批次曾共享单执行槽。
两端可评分状态一致，但不能称分数逐位一致：16 组跨平台最高分差异超过 1e-6，最大
绝对差约 0.01953。两端 OpenCV/NumPy/ONNX Runtime 版本号相同，差异尚未逐帧归因，
不能直接断言由某一个依赖导致。对差异较大的 pair-060、093 分别重跑，两端各自复现了
自己的原分数。正式阈值标定应固定实际部署镜像/平台，不以本地分数替代服务器标定结果。

全部现场回归、浏览器及旧接口冒烟之后，两个容器仍 healthy、RestartCount=0；API 临时
目录文件数为 0。全部能力按需加载后的资源快照：API 约 1.953 GiB / 6 GiB、119 PID/线程，
测试台约 19.66 MiB / 256 MiB、1 PID/线程，均在既定限制内。

### 切换异常与回滚证据

首次切换端口检查失败时，脚本自动恢复两个原容器且 ID 不变；无关历史 18001 未修改。
在服务器随机回环端口复现了旧探针将 TIME_WAIT 判为占用的行为。修复为 Linux
SO_REUSEADDR 检查（不启用 SO_REUSEPORT，仍拒绝活跃监听）并有限等待 Docker 释放端口。
二次候选与切换成功。部署工具测试 23 passed、1 个 Linux 专用用例在 Windows 跳过；
对应真实 TIME_WAIT/活跃监听保护已在 Linux 服务器以标准库单独验证。

保留的回滚容器：

- `face-algorithm-task-2026-001-prev-872c969`，原 ID `74598b79c79df1dcbf4bf04b78df4ca0616d40dc4c49fe1bb80ce0fc95c47f2b`。
- `face-algorithm-visual-console-task-2026-001-prev-872c969`，原 ID `bdcfca42e72ea59778aa53e7cc8fdc62e20b833122254bdd167ee2007f2f5844`。

两者目前停止保留，原镜像和配置未删除；更早的备份不动。发现新服务持续不就绪、
契约错误或业务回归时，先停止接入新请求，再由 yzq 在 37 执行：

```bash
python3 /home/<user>/deployments/face-algorithm-task-2026-001/deploy-presence-release-1e6b57b.py \
  --commit 1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b --mode rollback
```

脚本先校验两组容器归属，再移除本次新容器并恢复原容器；不删除新旧镜像或源码，不操作
18001。恢复后重新检查两端口健康；旧版没有 presence，新调用方需暂停新能力。

保护对象 18001：容器 `d6773816222a2200ee76b0b23f35af51786cc64a46147e04933ff7d3281ece7f`、
镜像 `sha256:aff788a4523ead8cfa1cbfa6b37a1004386bc12b0e1bff3e01cc9acfe572bc1c`、
启动时间 `2026-09-15T09:43:38.483333895Z`、RestartCount=0 均与发布前相同。
