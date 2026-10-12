# 特征比对与 1:N 检索接口

`POST /api/compare_feature` 比较两个特征；`POST /api/search_feature` 比较一个待查询特征与本次请求的 N 个候选。均使用 `Content-Type: application/json`，不保存候选库、不查询系统人员资料、不判定身份通过。现有检测、提取、模型与质量处理流程保持不变。

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

## 纯特征 1:N 检索

服务 1.3.0 起提供 `POST /api/search_feature`。每次请求提供完整候选集，最多 1000 个；适合已有特征的批量比对。现有 Java `/api/users/search` 为照片到系统人员库的业务检索，输入与本接口不同。

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `probe_feature` | 是 | 待查询的 1024 维数值数组 |
| `probe_version` | 是 | `recognizer_v1_1024` |
| `reference_version` | 是 | 所有候选特征共同使用 `recognizer_v1_1024` |
| `candidates` | 是 | 1–1000 个 `{id, feature}` 对象 |
| `candidates[].id` | 是 | 1–128 字符的非空白字符串，请求内唯一；业务自行映射人员 |
| `candidates[].feature` | 是 | 该候选完整的 1024 维特征数组 |
| `top_k` | 否 | 默认 1；严格整数，范围 1–N |

所有特征遵循上面的有限数值、float32、非零范数和模型一致性约定。任一候选无效会拒绝整批；不跳过错误候选，也不返回未完成的局部排名。候选 ID 不作自动去空格或类型转换。JSON 重复键和额外字段同样拒绝。

成功返回 HTTP 200：

```json
{
  "success": true,
  "matches": [
    {"id": "candidate-a", "similarity_score": 1.0},
    {"id": "candidate-b", "similarity_score": 0.0}
  ],
  "candidate_count": 2,
  "top_k": 2,
  "feature_dimension": 1024,
  "feature_version": "recognizer_v1_1024",
  "matcher_version": "l2_dot_v1",
  "processing_time_ms": 8.5
}
```

示例分数与耗时仅说明字段。`candidate_count` 是本次完整比较的候选数；`matches` 恰有 `top_k` 项，按分数降序，相同最终分数保持候选输入顺序。负分也是合法结果，HTTP 200 不代表身份匹配。`processing_time_ms` 为服务端请求处理耗时，单位毫秒。接口不接收 threshold，也不返回输入特征。

请求体上限 32 MiB（33554432 字节），5 秒总预算覆盖接收、解析、校验和计算。每服务进程只有一个独立的 1:N 工作槽，无等待队列；大 JSON 解析和批量计算在独立轻量进程完成，图像模型不重复加载。槽在后台任务实际结束后才释放；客户端超时或取消不表示服务端已立即空闲。

错误结构沿用 1:1 的 `success / suggestion / reason_codes / message`。400 `INVALID_FEATURE`、413 `FEATURE_PAYLOAD_TOO_LARGE`、500 `INFERENCE_ERROR`、504 `ALGORITHM_TIMEOUT` 的含义不变；另外：

- 429 `FEATURE_SEARCH_BUSY`：已有 1:N 请求在上传或处理，有限退避后重试；不要并发无限重试。
- 503 `FEATURE_SEARCH_UNAVAILABLE`：独立工作进程未就绪或不可用，检查服务。

连续调用建议逐个发送并复用 HTTP 连接，单个请求合并完整候选集。传输时间也计入 5 秒预算，低带宽客户端应减少每次候选数量。上限不代表吞吐量承诺；每次传递候选不会跨请求缓存成特征库。

### 可直接运行的联调示例

下例构造三组完整 1024 维向量用于接口联调；业务接入应替换为特征提取接口的真实 `feature` 数组。

```python
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

probe = [1.0] + [0.0] * 1023
payload = {
    "probe_feature": probe,
    "probe_version": "recognizer_v1_1024",
    "reference_version": "recognizer_v1_1024",
    "candidates": [
        {"id": "same", "feature": probe},
        {"id": "orthogonal", "feature": [0.0, 1.0] + [0.0] * 1022},
        {"id": "opposite", "feature": [-1.0] + [0.0] * 1023},
    ],
    "top_k": 3,
}
request = Request(
    "http://<server-37>:18001/api/search_feature",
    data=json.dumps(payload, allow_nan=False).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urlopen(request, timeout=10) as response:
        print(json.load(response)["matches"])
except HTTPError as error:
    print(error.code, json.load(error))
```

预期依次返回 same、orthogonal、opposite，分数约为 1、0、-1。Apifox 等调试工具选择 POST、Body → JSON，按字段传入数值数组，不使用 form-data；完整 JSON 必须由实际数值组成，不能保留省略号或 Python 的列表运算表达式。

## 启动与升级

完整源码部署使用 `python feature_comparison_server.py --port 8000`；根 Compose 对应的默认 Dockerfile 已使用此入口。包装入口要求原 `feature_server.py` 版本为 1.1.0，复用其 app、生命周期和图像推理槽，追加 1:1 和 1:N 路由，公开服务版本更新为 1.3.0。直接运行原 `feature_server.py` 仍是原两接口服务。

现有生产镜像可使用 `Dockerfile.compare` 追加接口，支持现场 Python 3.10、Pydantic 2、FastAPI 和 NumPy 环境，不安装或升级推理依赖。构建上下文为本目录：

```sh
docker build -f Dockerfile.compare \
  --build-arg BASE_IMAGE=<原1.1.0镜像的固定标签或摘要> \
  --build-arg VCS_REF=<源码提交SHA> \
  -t face-algorithm:<发布标签> .
```

必须核对基础镜像身份；此构建只复制比较源码闭包及包装入口，保留原 `feature_server.py`、特征提取器、模型和配置。先在临时容器验证，再把现场 Compose 的 `face-algorithm` 镜像更新为发布镜像，执行 `docker compose up -d --no-deps --no-build face-algorithm`。保留现场端口、挂载、网络、资源限制和健康检查，不用仓库示例 Compose 覆盖现场文件。

上线检查 `/openapi.json` 包含新增 POST 路由、`/ready` 就绪、比较正常及错误输入行为，并用既有联调图片比对升级前后的检测和提取结果。回退时恢复原镜像及其启动配置，只重建 `face-algorithm`；不涉及数据库变更。
