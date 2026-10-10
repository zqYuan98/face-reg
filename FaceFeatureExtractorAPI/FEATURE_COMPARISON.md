# 特征比对接口与兼容部署

新增 `POST /api/compare_feature`，使用 `Content-Type: application/json`。现有检测、提取、模型与质量处理流程保持不变。该接口使用已有的 L2 归一化点积算法，只返回相似度，不查询人员库或判断身份通过。

## 请求与响应

四个必填字段：

| 字段 | 类型与要求 |
| --- | --- |
| `reference_feature` | 长度为 1024 的数值数组 |
| `probe_feature` | 长度为 1024 的数值数组 |
| `reference_version` | `recognizer_v1_1024` |
| `probe_version` | `recognizer_v1_1024` |

数值须有限、在 float32 范围内，转换后的向量范数非零。拒绝布尔值、字符串、嵌套数组、重复或多余字段及不匹配的版本；调用方须确认两侧确实来自相同模型和预处理。JSON 整体最多 128 KiB（131072 字节），接收、解析与计算的总期限为 5 秒。

成功为 HTTP 200：

```json
{
  "success": true,
  "similarity_score": 1.0,
  "feature_dimension": 1024,
  "feature_version": "recognizer_v1_1024",
  "matcher_version": "l2_dot_v1",
  "processing_time_ms": 0.3
}
```

相似度范围为 `[-1, 1]`；同方向、相反方向、正交向量分别得到 `1`、`-1`、`0`。处理耗时单位为毫秒，实际值由运行结果决定。接口不返回输入特征或通过阈值。

| HTTP 状态 | `reason_codes` |
| --- | --- |
| 400 | `INVALID_FEATURE` |
| 413 | `FEATURE_PAYLOAD_TOO_LARGE` |
| 500 | `INFERENCE_ERROR` |
| 504 | `ALGORITHM_TIMEOUT` |

错误对象包含 `success: false`、`suggestion: "REVIEW"`、`reason_codes` 数组和 `message`。原图像接口仍使用各自的既有错误格式。

## 启动与升级

完整源码部署使用 `python feature_comparison_server.py --port 8000`；根 Compose 对应的默认 Dockerfile 已使用此入口。包装入口要求原 `feature_server.py` 版本为 1.1.0，复用其 app、生命周期和图像推理槽，仅追加比较路由，公开服务版本更新为 1.2.0。直接运行原 `feature_server.py` 仍是原两接口服务。

现有生产镜像可使用 `Dockerfile.compare` 追加接口，支持现场 Python 3.10、Pydantic 2、FastAPI 和 NumPy 环境，不安装或升级推理依赖。构建上下文为本目录：

```sh
docker build -f Dockerfile.compare \
  --build-arg BASE_IMAGE=<原1.1.0镜像的固定标签或摘要> \
  --build-arg VCS_REF=<源码提交SHA> \
  -t face-algorithm:<发布标签> .
```

必须核对基础镜像身份；此构建只复制比较源码闭包及包装入口，保留原 `feature_server.py`、特征提取器、模型和配置。先在临时容器验证，再把现场 Compose 的 `face-algorithm` 镜像更新为发布镜像，执行 `docker compose up -d --no-deps --no-build face-algorithm`。保留现场端口、挂载、网络、资源限制和健康检查，不用仓库示例 Compose 覆盖现场文件。

上线检查 `/openapi.json` 包含新增 POST 路由、`/ready` 就绪、比较正常及错误输入行为，并用既有联调图片比对升级前后的检测和提取结果。回退时恢复原镜像及其启动配置，只重建 `face-algorithm`；不涉及数据库变更。
