# Face Algorithm

面向私有化 CPU 部署的模块化人脸算法包及薄 FastAPI 适配层。算法模块不访问数据库、
不判断合同状态，也不创建业务工单。

## 接口入口（2026-09-28 候选版本）

公司内网 API：`http://<server-37>:18002`；人工测试台：`http://<server-37>:18003`。
presence 为本次候选新增能力，发布 commit 和线上验证待补；旧 `18001` 不属于新开发入口。

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
worker 调用。presence 首版固定 `UNCALIBRATED + INCONCLUSIVE + found=null`，高分和无脸
均不能作身份通过/排除结论，也无活体或反翻拍保证。业务先检查 HTTP，再读取
`decision/found`；不要把 `success` 当“同一人”。完整字段、错误、安全边界和示例见下方文档。

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
