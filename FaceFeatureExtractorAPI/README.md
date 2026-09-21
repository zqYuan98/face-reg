# Face Algorithm

面向私有化 CPU 部署的模块化人脸算法包及薄 FastAPI 适配层。算法模块不访问数据库、
不判断合同状态，也不创建业务工单。

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
