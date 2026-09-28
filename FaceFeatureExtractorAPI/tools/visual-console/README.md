# 人脸算法可视化测试台

这是独立于 `face_algorithm` 和 FastAPI 适配层的内网测试工具。浏览器只访问本测试台；
测试台通过固定白名单把六条公开 v2 请求流式转发给算法服务。服务端仅使用 Python
标准库，不写入上传媒体，也不接受浏览器指定的上游地址。

2026-09-28 presence 已部署（算法提交 `1e6b57b5b1dd`）；入口分别为算法
`http://<server-37>:18002` 与测试台 `http://<server-37>:18003`。
完整请求响应见 [API 联调文档](../../docs/TASK-2026-001-API-DEBUGGING.md)。

## 配置

启动时必须固化以下环境变量：

| 环境变量 | 必填 | 示例 | 说明 |
|---|---|---|---|
| `VISUAL_CONSOLE_UPSTREAM` | 是 | `http://<server-37>:18002` | 算法服务 origin，不允许路径、查询或凭据 |
| `VISUAL_CONSOLE_PUBLIC_ORIGIN` | 是 | `http://<server-37>:18003` | 浏览器实际访问的精确 origin |
| `VISUAL_CONSOLE_HOST` | 否 | `0.0.0.0` | 进程监听地址，默认 `127.0.0.1`；容器默认覆盖为 `0.0.0.0` |
| `VISUAL_CONSOLE_PORT` | 否 | `8080` | 进程监听端口，默认 `8080` |

`PUBLIC_ORIGIN` 的 authority 必须与浏览器请求的 `Host` 完全一致。测试台不发送 CORS
响应头；POST 还要求完全一致的 `Origin` 和有效的 `X-Visual-Console-CSRF`。

## 本地启动

Windows PowerShell，在本目录运行：

```powershell
$env:VISUAL_CONSOLE_UPSTREAM='http://127.0.0.1:8000'
$env:VISUAL_CONSOLE_PUBLIC_ORIGIN='http://127.0.0.1:8080'
$env:VISUAL_CONSOLE_HOST='127.0.0.1'
$env:VISUAL_CONSOLE_PORT='8080'
python .\server.py
```

Linux：

```bash
VISUAL_CONSOLE_UPSTREAM=http://127.0.0.1:8000 \
VISUAL_CONSOLE_PUBLIC_ORIGIN=http://127.0.0.1:8080 \
VISUAL_CONSOLE_HOST=127.0.0.1 \
VISUAL_CONSOLE_PORT=8080 \
python3 server.py
```

打开 `http://127.0.0.1:8080`。不要使用 `file://` 打开页面。

## HTTP 边界

只允许以下资源，且全部拒绝查询参数：

- 静态 GET：`/`、`/index.html`、`/styles.css`、`/core.js`、`/app.js`；
- 浏览器图标 GET：`/favicon.ico`（固定返回 `204`，无图标内容）；
- 控制台 GET：`/health/live`、`/api/console/session`、`/api/console/ready`；
- 代理 POST：`/api/v2/portrait/generate`、`/api/v2/witness/blur-background`、
  `/api/v2/verify/images`、`/api/v2/verify/video/quick`、
  `/api/v2/verify/video/full`、`/api/v2/verify/video/presence`。

先请求 `/api/console/session`，读取返回的 `csrf_token`。该令牌只保存在服务进程内存中，
15 分钟过期，重启即失效；POST 时放入 `X-Visual-Console-CSRF`。测试台不使用 Cookie。

代理要求单一且合法的 `Content-Type`、`Content-Length`，拒绝分块请求、`Expect`、重复
长度和异常 multipart boundary。浏览器使用 `FormData` 时不要手工设置
`Content-Type`。Cookie、认证头、内部令牌和代理头不会被转发。上游响应只保留经校验的
`Content-Type` 与 `Content-Length`，所有响应均带 `no-store`、固定 CSP 和其他安全头。

同一时刻只允许一条昂贵 POST；繁忙时返回 429。健康、会话、readiness 和静态资源不受
该并发槽影响。请求与响应按固定大小块传递，不创建临时媒体文件。

## 视频能力与结果

- “视频目标出现”使用 `video_presence` readiness，提交 `reference_image + video`，检查目标
  候选是否曾出现在采样范围；quick/full 保留，仍是持续身份一致性，不是此需求。
- presence 显示 `decision/found`、最佳分数/时刻、扫描完整度、采样间隔与计数、最多 5 行数字
  证据。所有单元格由 `textContent` 创建，拒绝非数值证据，不解析上游 HTML。
- 首版 `UNCALIBRATED` 固定为 `INCONCLUSIVE/found=null`，即便高分、无可用脸或扫描完整也
  不显示 PASS；FOUND/NOT_FOUND_IN_SCAN 为保留态，当前不能产出。没有活体/反翻拍保证。
- presence/full 服务算法预算 600 秒、代理总预算 620 秒、浏览器请求截止 630 秒；三种视频
  请求体均为 540,082,176 字节上限、响应 4 MiB。超时后不立即并发重试。
- readiness 返回五个能力键（quick/full 共享一个），页面按六个任务计算可用数量；缺失
  `video_presence` 时仅新入口不可用，旧功能保持可用。

## 最小容器

从本目录构建。镜像只复制服务器和五个静态文件，不含算法代码、模型、测试或开发依赖：

```powershell
docker build -t face-algorithm-visual-console:task-2026-001 .
```

示例运行命令；生产验收时应继续显式限制 CPU、内存、PID、日志轮转并绑定确认过的单一
网卡地址：

```powershell
docker run --rm --read-only --cap-drop ALL `
  --security-opt no-new-privileges:true `
  -p 127.0.0.1:18003:8080 `
  -e VISUAL_CONSOLE_UPSTREAM=http://host.docker.internal:18002 `
  -e VISUAL_CONSOLE_PUBLIC_ORIGIN=http://127.0.0.1:18003 `
  face-algorithm-visual-console:task-2026-001
```

Linux 部署应将上游和 `-p` 地址替换为已核验的主机单网卡 IP；浏览器不能使用 Docker
容器名作为 API 地址。该标准库服务器只面向可信内网，不具备公网所需的 TLS、身份认证、
来源 ACL 和审计能力。

## 测试

服务器测试不需要安装第三方包：

```powershell
python -m unittest discover -s tests -p "test_server.py" -v
node --test tests/core.test.mjs
```

测试使用本机临时 HTTP 服务，不读取算法模型或客户媒体。
