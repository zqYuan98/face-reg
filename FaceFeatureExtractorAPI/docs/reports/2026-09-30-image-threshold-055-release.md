# TASK-2026-001 图片比对 0.55 试用阈值发布记录

状态：**已正式部署并完成下述技术验收**。2026-09-30 北京时间15:50切换37的18002/18003，API 2.4.0，代码 `a49670dc91234a7ac599c7e9628614c02be4b885`；两容器 healthy、8模型就绪。文档在随后单独提交，不改变已发布代码身份。

## 范围及接入

- API 2.4.0；`POST /api/v2/verify/images` 的 `reference_image` / `probe_image` 上传方式及现有返回字段名不变。
- 增加图片专属 `calibration.status=CONFIGURED_UNVALIDATED`；两张有效图片的分数 >= 阈值时返回 `IMAGE_TRIAL_THRESHOLD_MATCHED`，否则 `IMAGE_TRIAL_THRESHOLD_NOT_MATCHED`。两者都是 `success=true`、`suggestion=REVIEW`，不是正式标定通过。
- 服务端显式配置 `image_verification.mode=configured`、`threshold=0.55`；需重启绑定配置，不支持 HTTP 请求级覆盖。源码通用默认仍为 uncalibrated。
- 不可比维持无分数及原质量/系统原因，不归为未匹配。签名标定元数据为 null，不伪造 ACTIVE。
- 视频 presence 仍 0.5、原阈值 ID；quick/full 标定规则、白底 face_archive/MODNet 与资源限制不变。
- 只在当前工作分支发布，不合并 main、不创建标签；用户媒体、密钥、配置文件和构建产物不入 Git。

## 本地验证

各模块先取得 RED 后实现 GREEN。最终源码全量：`python -m pytest -q --tb=short` → **1890 passed、1 skipped、10 warnings**，113.39 秒。跳过项为 Windows 上的 Linux TCP TIME_WAIT 语义；警告为既有 Starlette/httpx 与 tarfile 兼容提醒。

`node --test tools/visual-console/tests/core.test.mjs` → **38 passed**。部署单元测试 **184 passed、1 skipped**；算法配置/图片模块 **244 passed**，DTO/API **203 passed**（这些是全量测试的子集，不累加）。

覆盖边界0.5499999/0.55/0.5500001、阈值0/1、负分、非法数值/布尔/字符串、原签名与未标定、质量和硬错误优先、side/旧模式/视频试用码禁入、runtime状态失效至HTTP500、伪造DTO拒绝、测试台错误/重置。独立规格审查、随后独立代码质量审查均通过，无阻塞问题。

## 部署基线与回滚边界

直接升级基线是代码 `4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a` / API 2.3.1，不是更早的2557版本。

- 原API完整ID：`1cf05a422d89290099d03b2bead2cd9396e0451e7ca26beef68fba1bf23f150e`。
- 原测试台完整ID：`e4af1efbeae7b5d189e00838f48b2a9b39aa320882f075e508d7b6121eef8185`。
- 原运行配置SHA256：`6d300c20cac3158ce1c5a720eb92f6a9231e3915e2a81e90bfe2653226cf7b5d`。
- 受保护18001完整ID：`d6773816222a2200ee76b0b23f35af51786cc64a46147e04933ff7d3281ece7f`；启动时间 `2026-09-15T09:43:38.483333895Z`，重启数0。

发布脚本先验证完整基线，再严格比较完整YAML，仅允许 image_verification 的模式和 .55 阈值差异；先候选回环18012，验收后切换18002/18003。旧配置与旧容器保存；失败自动恢复原4e6a容器并校验2.3.1和8模型就绪。更早备份及18001不动。

## 现场验收与最终身份

公司分支 `feat/TASK-2026-001-modular-face-algorithm-api` 已推送固定代码提交。部署使用该提交的 git archive，未把工作区文档草稿混入镜像。

| 身份 | 实测值 |
|---|---|
| 代码 | `a49670dc91234a7ac599c7e9628614c02be4b885` |
| archive SHA256 | `7594b3c7b9803e647e63e56a7170e8acb8bb04313e132bb2e0fa3d3a8cbb5c38` |
| 新配置 SHA256 | `3b7cca56b90922fc83ec00665b5be4a32b2fb2614bbeb75a04724ff6ffb0c5c2` |
| API容器 | `6e76234c7b06d4eaba0395d368ba9777263d346d418f6221130aa4541596fc13` |
| API镜像ID | `sha256:be0f4b204a2bb9344118006abdfa8411ea76fc9abb1ecd6899de86c0f85573f2` |
| 测试台容器 | `a398b1a59d856ffdc58128053737e1b7cfe7816d8df101fc25bda0b6dd6a4af3` |
| 测试台镜像ID | `sha256:9bc90861e5da936f75e61025bfbf9b1a6b8df720b00bbd0831e16a45bb33d9cd` |

API启动UTC07:50:27.584826471、测试台UTC07:50:34.289925885，均重启数0。资源和只读挂载维持原配置；临时文件最终检查为0。18001身份、镜像、启动时间及重启数不变。候选容器已移除，任务专属SSH转发和浏览器会话已关闭；旧4e6a两容器已停止，命名后缀 `-prev-4e6a3372ddeb`，可恢复。更早备份未改动。

### HTTP与模型验收

候选回环18012通过后才切换正式服务；正式18002再次执行相同图片验收。现有OpenAPI请求/响应字段集合不变，只增加指定图片状态、两个原因码、相关说明及版本；客户端须更新枚举解析，不能把这称作契约完全无变化。

| 样本 | 正式响应 | 分数/阈值 |
|---|---|---|
| 授权参考照片与自身 | HTTP200，true / REVIEW / IMAGE_TRIAL_THRESHOLD_MATCHED | 1.0 / .55 |
| 授权不同人员组照片 | HTTP200，true / REVIEW / IMAGE_TRIAL_THRESHOLD_NOT_MATCHED | 0.349888008910748 / .55 |
| 参考照片+无脸图 | HTTP200，true / RECAPTURE / NO_FACE | null / .55 |
| 原打卡白底反馈图 | HTTP200，成功480×640 JPEG，REVIEW / PORTRAIT_SOURCE_LIMITED | 不属于身份比分 |

候选另完成6条v2的smoke：portrait、witness、images、video/quick、video/full、video/presence；presence在合成MP4/AVI/MOV中均FOUND且.5，quick/full仍非PASS。6张白底定向样本（非随机批次）5张成功，空白无脸正确拒绝；本次没有重跑历史20张随机照片或100组视频，不复用其数量作为当前证据。

正式视频三组复核均HTTP200，阈值.5、ID `TASK-2026-001-presence-trial-050-20260928`、策略指纹 `9f6137e6298f06a8b1e705842f26b9daa06e5dcf227fd37adb68fcf04375cf9c` 不变：

- 匿名001：FOUND/true，最高分0.8814782441834548，15次比较，REVIEW。
- 匿名020：NO_COMPARABLE_FACE，INCONCLUSIVE/null，REVIEW。
- 匿名046：INVALID_REFERENCE，INCONCLUSIVE/null，RECAPTURE。
- 三组均 `liveness_evaluated=false`；不增加活体检测或签名门槛。

### 浏览器验收

正式18003实际上传同图与不同组图片，分别显示“达到试用匹配阈值”“未达到试用匹配阈值”，实际分数、0.5500、1024维、试用精度未验证和人工复核提示齐全，保留REVIEW。重置后两张预览及结果清空、提交禁用，浏览器console为0错误/0警告。

现场还观察到测试台既有行为：图片模式仅移除/替换单个素材时，旧结果仍保留到重跑或重置；不要把旧结果当作新素材的判断。联调采用**先重置，再上传并点击开始比对**。此项非本次阈值算法变更，未扩大修改范围；重置路径已实测通过。截图和授权媒体仅留在本机被忽略输出目录，不入Git。

## 回滚（仅获授权时执行）

以下命令运行在37，使用本次唯一 helper 与固定提交；不要使用更早发布的 helper：

```bash
python3 /home/<user>/deployments/face-algorithm-task-2026-001/deploy-image-threshold-release-a49670dc91234a7ac599c7e9628614c02be4b885.py \
  --commit a49670dc91234a7ac599c7e9628614c02be4b885 --mode rollback
```

它恢复上述原4e6a/API2.3.1完整ID与旧配置SHA，图片恢复uncalibrated、presence保持.5及原ID。回滚前校验所有当前/备份归属，恢复后校验版本与8模型/能力健康；不触碰18001。上线流程已验证保护逻辑和失败回滚单测，**未为演练主动中断当前正式服务执行回滚**。

任何真实样本技术回归只证明处理链路和指定阈值行为，不代表人脸识别精度、误报率或生产标定已经验证。
