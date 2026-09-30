# Face Algorithm

面向私有化 CPU 部署的模块化人脸算法包及薄 FastAPI 适配层。算法模块不访问数据库、
不判断合同状态，也不创建业务工单。

## 接口入口（API 2.4.0）

2026-09-30 服务器 37 的 `18002/18003` 已正式部署 API `2.4.0` 图片独立试用阈值，
代码为 `a49670dc91234a7ac599c7e9628614c02be4b885`。
发布代码、健康状态、各项实测与回滚入口以 [图片 0.55 试用阈值发布记录](docs/reports/2026-09-30-image-threshold-055-release.md) 为准。
直接升级基线与回滚目标为 API `2.3.1`、提交 `4e6a3372ddeb0a53d4f3e024e0e072f636ae9f1a`，
须恢复原 API/测试台与升级前完整配置，不使用更早的 `2557e17 / 2.3.0` 回滚入口。
37 本次仅修改图片 `mode=configured`、`threshold=0.55`，两侧 `entry_verify`、presence `0.5`/原 ID、
portrait `face_archive`/MODNet 和其余配置保留；`18001` 不参与切换。

白底人脸照沿用 `face_archive`，不强制完整头顶/颈部/肩部；
保持原接口、`image` 上传字段和响应字段，2.3.0 已引入 `PORTRAIT_SOURCE_LIMITED` / `PORTRAIT_UPSCALE_LIMITED` 原因枚举。
成功出图可为 `success=true + REVIEW`，提醒在 `reason_codes`；客户端按 `success=true && image_base64`
判断出图，不能仅凭 REVIEW 判定失败。
2.4.0 沿用 2.3.1 白底修正：`face_archive` 每侧检测框溢出不超过原框对应宽/高的 5%，
图内可见面积至少为原框的 90%，目标选择和五点几何有效、五点均在源图内、裁后宽高均至少 112px，
且抠图通过时，可生成 `REVIEW + PORTRAIT_SOURCE_LIMITED`。严重越界、坏关键点或坏抠图仍失败；
检测框越界本身不证明真实脸部缺失。只使用源图真实像素，不补画、不提取 embedding。
2.3.1 白底补丁本身不新增字段或原因枚举，保留 480×640 JPEG/30 秒预算、严格 `head_shoulders` 和其他能力的质量门禁。
2.3.1 的历史授权原图技术结果见 [白底检测框修正记录](docs/reports/2026-09-30-portrait-box-overflow-release.md)，
不充作 2.4.0 验收结果或识别准确率、延迟保证。

图片 `configured` 模式仍上传 `reference_image`、`probe_image`，不新增 HTTP 阈值参数或响应字段。
两侧可比时返回 `calibration.status=CONFIGURED_UNVALIDATED`、`decision_threshold=0.55`，签名 ID/digest 均为 null；
分数 `>=0.55` 为 `IMAGE_TRIAL_THRESHOLD_MATCHED`，低于为 `IMAGE_TRIAL_THRESHOLD_NOT_MATCHED`，
均为 `success=true + REVIEW`。不可比时分数 null、保留原质量/错误原因，不当作未匹配。
试用精度未验证、业务仍需人工复核；源码默认仍为 `uncalibrated`，服务端配置变更需重启/重建服务。

公司内网 API：`http://<server-37>:18002`；人工测试台：`http://<server-37>:18003`。
更早历史：2026-09-28 以上 `18002/18003` 已发布 `2.2.0` 且均 healthy，发布代码提交为
`d78e723b8fb8737b09e12859e57aa9856a640db6`；旧 `18001` 未改动，不属于新开发入口。
该次镜像、回归与历史回滚记录见 [2.2.0 历史发布记录](docs/reports/2026-09-28-presence-best-effort-release.md)。
2.2.0 引入的低质量尽力比对、补采和试用阈值三态能力在 2.4.0 保持不变。37 在 2026-09-28 随后按用户指定将 presence 运行配置
设为试用阈值 `0.5`（`configured`），返回 `CONFIGURED_UNVALIDATED`，业务仍需人工 REVIEW。
该次阈值调整仅改变 presence 运行配置，未改代码版本、源码默认无阈值配置、images/quick/full、18001 或测试台。
本次 2.4.0 升级继续保留 `0.5` 及原阈值 ID，不用仓库默认 null 覆盖运行配置。
配置 ID、验证与配置回退见 [0.5 试用阈值记录](docs/reports/2026-09-28-presence-threshold-050.md)，生产精度未验证。
2026-09-28 历史本地/匿名现场功能验证及验收边界另见
[2.2.0 本地验证记录](docs/reports/2026-09-28-presence-best-effort-validation.md)，不能充作 2.4.0 或人工标签和阈值验收证据。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health/live`、`/health/ready`、`/health` | 存活、模型/能力就绪、ready 兼容别名 |
| GET | `/openapi.json` | 本轮契约 `info.version=2.4.0`，部署后须核对；`/docs`、`/redoc` 关闭 |
| POST | `/api/v2/portrait/generate` | 考勤归档白底人脸照，成功图片可附 REVIEW 提醒 |
| POST | `/api/v2/witness/blur-background` | 人物优先背景虚化 |
| POST | `/api/v2/verify/images` | 双图身份比对 |
| POST | `/api/v2/verify/video/quick` | 持续身份一致性快速抽样 |
| POST | `/api/v2/verify/video/full` | 持续身份一致性完整检查 |
| POST | `/api/v2/verify/video/presence` | 视频目标出现候选证据，非持续一致性 |
| POST | `/api/detect_face` | legacy 检测，按 detect_face_enabled 注册（默认开） |
| POST | `/api/extract_feature` | legacy 特征，默认关闭/404，不为新功能开启 |

六个 v2 使用 multipart 文件上传；presence 与 full 都有 600 秒算法截止，正式业务由异步
worker 调用。沿用 2.2.0 的 presence 规则：亮暗/模糊/姿态/小脸/贴边为软提示，可形成有效 1024 维特征就比较；
参考 112px、视频 64px 不是硬拒绝门槛。默认 0.5 秒基础采样、每窗口最多补采 2 帧，1200 帧预算
包含首尾与补采；长视频必要时增大实际基础间隔。每帧最多 5 候选，局部候选失败保留其他证据。

服务端显式 `configured` 阈值和 ID 返回 `CONFIGURED_UNVALIDATED`，支持
`FOUND/true`、`NOT_FOUND_IN_SCAN/false`、`INCONCLUSIVE/null`；请求不接受调用方阈值。
使用源码默认无阈值配置时仍计算证据，返回 `THRESHOLD_NOT_CONFIGURED + INCONCLUSIVE/null`，没有隐含默认阈值。
扫描未完成、无可比脸、参考无效或全局失败不能产生肯定/否定结果；候选截断/局部失败阻止未命中结论。
业务先检查 HTTP，再读取 `decision/found`；分数不是概率，`success` 不是同一人，`suggestion=REVIEW`
不是“未匹配”，试用结果不得业务自动 PASS。presence 不要求活体或签名校准门禁，不保证反翻拍；
图片原 artifact/uncalibrated、quick/full 的签名 calibration 与严格质量规则不变，新增图片试用模式也不放宽质量门禁。
完整字段、错误和示例见下方文档。

## 本地验证

要求 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
```

启动单进程服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.app:create_default_app --factory `
  --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --no-access-log
```

开发联调请先看
[docs/TASK-2026-001-API-DEBUGGING.md](docs/TASK-2026-001-API-DEBUGGING.md)；接口安全边界、
接入责任、阈值标定、隐私和容量约束见
[docs/ALGORITHM_API_INTEGRATION.md](docs/ALGORITHM_API_INTEGRATION.md)。见证照背景虚化的
内存边界、证卡检测生产验收门槛见
[docs/witness-blur-deployment.md](docs/witness-blur-deployment.md)。

人工标签、按人员隔离的 tuning/evaluation 与离线汇总工具见
[docs/PRESENCE_LABEL_VALIDATION.md](docs/PRESENCE_LABEL_VALIDATION.md)。同名文件仅有关联，
不是出现真值；无人工标签只能报告功能/覆盖，不能宣称现场准确率。

## CPU 容器

```powershell
docker build -t face-algorithm:cpu .
docker run --rm -p 8000:8000 `
  --read-only --tmpfs /app/tmp:rw,noexec,nosuid,size=2g `
  -v C:\secure\checkpoints:/app/checkpoints:ro `
  -v C:\secure\algorithm.yaml:/app/config/algorithm.yaml:ro `
  face-algorithm:cpu
```

镜像固定 Python 3.12、CPU 版 ONNX Runtime/PyTorch、非 root 用户和一个 Uvicorn
worker。不要在同一环境安装 `onnxruntime` 与 `onnxruntime-gpu`。
500 MiB 视频在 multipart spool 与算法私有路径之间会短时占用接近两份空间，因此示例
临时盘不是 512 MiB；实际容量仍须按目标机并发、媒体上限和容器内存限制压测。
