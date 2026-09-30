# 人脸算法可视化测试台

2026-09-30 测试台与算法服务已正式部署 API `2.3.1` 白底检测框越界修正（代码 `4e6a337`），
服务器 37 的 `18002/18003` 均 healthy；portrait 已启用 `face_archive`。
不强制肩部，按 `success=true && image_base64` 判断出图；成功 REVIEW 显示质量提醒但保留图片与下载。
部署状态与各项验收完成状态以 [检测框越界修正发布记录](../../docs/reports/2026-09-30-portrait-box-overflow-release.md) 为准。
直接升级基线与回滚目标为 `2.3.0 / 2557e17`，原 API/测试台容器已停止保留，回滚恢复原 presence `0.5` 配置；
本次配置字节哈希不变，不以更早的 `d78 / 2.2.0` 为本轮回滚目标。

2.3.1 补丁仅修正 `face_archive` 的轻微检测框外扩：每侧溢出不超过原框对应宽/高的 5%、
可见面积至少 90%，且有效五点均在图内、裁后宽高均至少 112px、抠图通过时，返回有图的
`REVIEW + PORTRAIT_SOURCE_LIMITED`；测试台须保留预览、下载及提醒。
严重越界、坏关键点或坏抠图仍失败无图；不能据框越界断言真实脸部缺失，也不能把 FACE_CROPPED
统一显示为成功。补丁不补造像素、不提取 embedding，不新增字段或枚举，复用 2.3.0 已有原因码；
严格 `head_shoulders` 与其他五个 v2 接口不变。正式测试台已用反馈原图验证预览和实际下载，
显示“已生成，存在质量提醒”，浏览器往返约 1.34 秒；详细证据与限制见本次发布记录。

这是独立于 `face_algorithm` 和 FastAPI 适配层的内网测试工具。浏览器只访问本测试台；
测试台通过固定白名单把六条公开 v2 请求流式转发给算法服务。服务端仅使用 Python
标准库，不写入上传媒体，也不接受浏览器指定的上游地址。

更早历史：测试台在 2026-09-28 按 API `2.2.0` presence 契约发布，当时算法入口
`http://<server-37>:18002` 与测试台 `http://<server-37>:18003` 均 healthy，
发布代码提交为 `d78e723b8fb8737b09e12859e57aa9856a640db6`；发布证据、浏览器验收状态与回滚入口见
[2.2.0 历史发布记录](../../docs/reports/2026-09-28-presence-best-effort-release.md)。
37 的 presence 上游运行配置随后已按用户指定设为试用阈值 `0.5`（`configured`），返回
`CONFIGURED_UNVALIDATED`，业务仍需人工 REVIEW；显示匹配结论不等于生产精度已经验收。
该次阈值调整未改测试台、18001 或 images/quick/full，源码默认无阈值不变。
本次 2.3.1 沿用 2.3.0 的白底人脸照展示，保留 presence `0.5` 与原阈值 ID；配置依据见
[0.5 试用阈值记录](../../docs/reports/2026-09-28-presence-threshold-050.md)。
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
- 沿用 2.2.0 presence 契约，以 `decision/found` 为主标题：`FOUND/true`、`NOT_FOUND_IN_SCAN/false`、
  `INCONCLUSIVE/null`。正常试用结果 `suggestion=REVIEW` 是次级业务审核提示，不覆盖 FOUND，
  也不能把 FOUND 渲染成业务自动 PASS。
- 服务端 `configured` 返回 `CONFIGURED_UNVALIDATED`、显式阈值/ID 与策略指纹；页面显示
  “试用阈值（精度未验证）”，不能把最高分格式化成准确概率，也不接受浏览器指定阈值后重算结果。
  使用源码默认 `uncalibrated` 的实例仍显示已算证据并附 `THRESHOLD_NOT_CONFIGURED`；仅在 `success=true`、
  `scan_complete=true`、`comparison_status=COMPARED` 时主显示为“比对已完成，尚未配置判定阈值”。
  失败、参考无效、扫描未完成、无可比脸的标题优先，不能无条件显示已完成；不提示缺少有效签名或活体失败。
- 扫描不完整/全局失败/无可比脸/参考无效只能是 INCONCLUSIVE/null，已有 partial 证据可保留。
  普通解码/推理失败的 DTO 才可能带部分证据；硬截止/执行器超时仍是 HTTP 504 错误体，不保证证据。
  完整扫描且有命中时，其他候选局部失败不撤销命中；低于阈值但有候选截断/失败不得显示未匹配。
  未匹配限于本次有限扫描，不代表整段视频绝对不存在。
- 展示最佳分数/时刻、阈值、comparison_status、策略指纹、请求/实际基础间隔、
  base/supplemental/decoded/sampled/valid-face/attempted/compared/failed 计数和软质量提示；
  最多 5 行证据，各含真实帧号、时间、最佳候选序号、分数与受限 `quality_warnings`。
  可跳转本地预览到证据时刻，不新增服务端截图或媒体保存。所有单元格使用 `textContent`，不解析上游 HTML。
- 暗/糊/侧脸/小脸/贴边是软提示，不以视频 64px 为硬拒绝门槛；每窗口最多补采 2 帧，
  总默认 1200 分析帧包含首尾和补采，长视频实际基础间隔可高于请求的 0.5 秒。
  `liveness_evaluated=false` 表示未执行且接口不要求，不是门禁；仍无活体/反翻拍保证。
- 新页面兼容旧 2.1.0 响应的历史 INCONCLUSIVE/null 含义；缺失/未知/不一致的新契约显示协议错误，
  不将无效分数/计数/枚举升级为匹配成功。旧五入口与签名 calibration 展示规则不变。
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
三态用例中的阈值只用于 synthetic-only 夹具，不是推荐现场阈值；生产精度仍需独立人工标签，
见 [匿名标签与离线验证](../../docs/PRESENCE_LABEL_VALIDATION.md)。
