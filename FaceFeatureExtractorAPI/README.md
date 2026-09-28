# Face Algorithm

面向私有化 CPU 部署的模块化人脸算法包及薄 FastAPI 适配层。算法模块不访问数据库、
不判断合同状态，也不创建业务工单。

## 接口入口（API 2.2.0 已部署）

公司内网 API：`http://<server-37>:18002`；人工测试台：`http://<server-37>:18003`。
2026-09-28 以上 `18002/18003` 已发布 `2.2.0` 且均 healthy，发布代码提交为
`d78e723b8fb8737b09e12859e57aa9856a640db6`；旧 `18001` 未改动，不属于新开发入口。
当前镜像、回归与回滚命令见 [本轮发布验收记录](docs/reports/2026-09-28-presence-best-effort-release.md)。
本轮已上线低质量尽力比对、补采和试用阈值三态能力。37 随后按用户指定将 presence 运行配置
设为试用阈值 `0.5`（`configured`），返回 `CONFIGURED_UNVALIDATED`，业务仍需人工 REVIEW。
仅 presence 运行配置改变；代码版本、源码默认无阈值配置、images/quick/full、18001 和测试台不变。
配置 ID、验证与配置回退见 [0.5 试用阈值记录](docs/reports/2026-09-28-presence-threshold-050.md)，生产精度未验证。
本地/匿名现场功能验证及验收边界另见
[本地验证记录](docs/reports/2026-09-28-presence-best-effort-validation.md)，不替代人工标签和阈值验收。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health/live`、`/health/ready`、`/health` | 存活、模型/能力就绪、ready 兼容别名 |
| GET | `/openapi.json` | 接入契约；`/docs`、`/redoc` 关闭 |
| POST | `/api/v2/portrait/generate` | 白底头肩照 |
| POST | `/api/v2/witness/blur-background` | 人物优先背景虚化 |
| POST | `/api/v2/verify/images` | 双图身份比对 |
| POST | `/api/v2/verify/video/quick` | 持续身份一致性快速抽样 |
| POST | `/api/v2/verify/video/full` | 持续身份一致性完整检查 |
| POST | `/api/v2/verify/video/presence` | 视频目标出现候选证据，非持续一致性 |
| POST | `/api/detect_face` | legacy 检测，按 detect_face_enabled 注册（默认开） |
| POST | `/api/extract_feature` | legacy 特征，默认关闭/404，不为新功能开启 |

六个 v2 使用 multipart 文件上传；presence 与 full 都有 600 秒算法截止，正式业务由异步
worker 调用。2.2.0 presence 的亮暗/模糊/姿态/小脸/贴边为软提示，可形成有效 1024 维特征就比较；
参考 112px、视频 64px 不是硬拒绝门槛。默认 0.5 秒基础采样、每窗口最多补采 2 帧，1200 帧预算
包含首尾与补采；长视频必要时增大实际基础间隔。每帧最多 5 候选，局部候选失败保留其他证据。

服务端显式 `configured` 阈值和 ID 返回 `CONFIGURED_UNVALIDATED`，支持
`FOUND/true`、`NOT_FOUND_IN_SCAN/false`、`INCONCLUSIVE/null`；请求不接受调用方阈值。
使用源码默认无阈值配置时仍计算证据，返回 `THRESHOLD_NOT_CONFIGURED + INCONCLUSIVE/null`，没有隐含默认阈值。
扫描未完成、无可比脸、参考无效或全局失败不能产生肯定/否定结果；候选截断/局部失败阻止未命中结论。
业务先检查 HTTP，再读取 `decision/found`；分数不是概率，`success` 不是同一人，`suggestion=REVIEW`
不是“未匹配”，试用结果不得业务自动 PASS。presence 不要求活体或签名校准门禁，不保证反翻拍；
旧 images/quick/full 的签名 calibration 与质量规则不变。完整字段、错误和示例见下方文档。

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
