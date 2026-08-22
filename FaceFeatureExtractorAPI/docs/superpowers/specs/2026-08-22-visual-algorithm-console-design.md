# TASK-2026-001 人脸算法可视化测试台设计

## 目标

为已经部署在 `<server-37>:18002` 的模块化人脸算法服务增加一个独立、可视化、
可直接验收的内网测试台。测试台部署在 `http://<server-37>:18003`，覆盖以下五项
能力：

1. 人脸白底头肩照生成；
2. 持证见证照背景虚化；
3. 两张图片身份比对；
4. 视频快速身份核验；
5. 视频完整身份核验。

测试台用于算法研发、交付验收和客户现场演示，不承担业务审批、客户媒体存储、合同
状态、人工复核流转或生产任务调度。

## 已确认的产品设计

采用“单任务工作台”布局和“专业实验室”视觉风格：

- 顶部显示测试台名称、算法服务地址和 readiness 摘要；
- 左侧固定列出五项能力，并按 readiness 中的能力状态禁用不可用项；
- 中部按能力展示单图、双图或图片加视频的拖放上传、内存预览和运行按钮；
- 右侧首先展示业务建议，再展示分数、质量、帧统计、校准状态和技术明细；
- 白底照和虚化照成功时展示派生图，并提供明确的用户主动下载按钮；
- 原始 JSON 默认折叠，使用纯文本安全渲染，便于算法开发排查；
- 页面明确标注“浏览器不持久化素材；素材会发送到测试台和算法服务临时处理，刷新或
  关闭页面会清空浏览器预览与结果”。

结果层级不能由 HTTP 状态或 `success` 单独驱动。页面必须以 `suggestion` 为主：

| suggestion | 中文主状态 | 页面动作 |
|---|---|---|
| `PASS` | 算法建议通过 | 绿色，但说明不等于最终业务审批 |
| `RECAPTURE` | 建议重新采集 | 橙色，并翻译 `reason_codes` |
| `REVIEW` | 建议人工复核 | 黄色，并解释阈值或身份不确定原因 |

视频结果的 `liveness_evaluated=false` 必须显示为“本接口未评估活体”，绝不能显示
“活体通过”。未校准环境即使相似度很高，也只能忠实展示
`REVIEW + THRESHOLD_UNCALIBRATED`。

## 方案比较与决策

### 方案一：浏览器跨域直连算法服务

需要给 18002 增加精确 CORS origin。实现最少，但页面端直接知道算法地址，跨域配置
容易因 IP、端口变化失效，且会把浏览器接入策略混入算法进程配置。本次不采用。

### 方案二：把静态页面并入算法 FastAPI

部署简单，但 UI 生命周期、缓存、安全响应头和算法服务耦合；前端问题可能影响算法
镜像与发布节奏，不符合“算法模块化”的要求。本次不采用。

### 方案三：独立测试台 sidecar 和同源代理（采用）

测试台是独立目录、独立进程、独立容器、独立端口。浏览器只访问 18003，测试台用
固定白名单把媒体流式转发到 18002。算法服务无需开启 CORS、无需引入页面依赖，也不
修改模型、算法包、阈值或 API 契约。

## 代码边界与文件结构

新增目录：

```text
FaceFeatureExtractorAPI/tools/visual-console/
├── index.html
├── styles.css
├── core.js
├── app.js
├── server.py
├── Dockerfile
├── .dockerignore
├── package.json        # 仅含开发期浏览器测试依赖
├── playwright.config.mjs
├── README.md
└── tests/
    ├── test_server.py
    ├── core.test.mjs
    ├── mock_upstream.py
    └── console.spec.mjs
```

- `index.html`、`styles.css`、`app.js`：无框架、无 CDN、无第三方遥测的浏览器界面；
- `core.js`：原因码翻译、状态推导、输入规格和响应格式化等纯函数；
- `server.py`：仅使用 Python 标准库，提供静态文件、精简 readiness 和受限流式代理；
- `Dockerfile`：生成不包含模型、算法代码和密钥的最小测试台镜像；
- `package.json` 和 Playwright 文件只用于开发期端到端测试，不进入运行镜像；
- 测试台不得 import `face_algorithm` 或 `api`，只依赖公开 HTTP v2 契约。

测试台文件不加入 Python wheel，避免成为算法库的运行依赖。

## 请求数据流

```text
局域网浏览器
  │  同源 HTTP：页面、readiness、multipart
  ▼
visual-console <server-37>:18003
  │  固定路径、固定上游、流式转发
  ▼
face-algorithm <server-37>:18002
  │
  └─ 结构化 JSON / 派生图 Base64
```

页面先请求 `/api/console/ready`。测试台读取上游 `/health/ready`，只返回：

- `status` 和 `ready`；
- ready 模型数量与总数；
- 各能力的 ready 状态；
- 本次检查时间。

不得向浏览器增加模型路径、哈希、配置文件路径或启动异常细节。

五条 POST 路径在测试台保持与算法服务一致，前端不允许配置任意上游地址：

| 功能 | 路径 | multipart 必填字段 | 算法截止时间 |
|---|---|---|---:|
| 白底照 | `/api/v2/portrait/generate` | `image` | 30 秒 |
| 见证照虚化 | `/api/v2/witness/blur-background` | `image` | 30 秒 |
| 两图比对 | `/api/v2/verify/images` | `reference_image`, `probe_image` | 30 秒 |
| 视频快检 | `/api/v2/verify/video/quick` | `reference_image`, `video` | 60 秒 |
| 视频全检 | `/api/v2/verify/video/full` | `reference_image`, `video` | 600 秒 |

浏览器把原始 `File` 直接加入 `FormData`，不手工设置 multipart `Content-Type`，保证
boundary 原样生成。代理保留 `Content-Type` 和 `Content-Length`，以固定大小块把请求体
发给上游；响应也分块写回浏览器，不把 500 MiB 视频或约 20 MiB Base64 JSON 整体复制
到代理内存。

测试台代理自身不创建媒体临时文件、不挂载持久卷。图片由算法服务在内存中解码；视频
会由算法服务临时写入权限受限的 `/app/tmp` tmpfs，正常完成、失败、超时和请求清理路径
均关闭并删除。算法容器异常重启时 tmpfs 随容器实例销毁。部署验收必须继续确认 18002
使用现有 tmpfs 而不是持久媒体目录。

## 输入限制

页面先做便于理解的前置校验，算法服务仍是权威校验方：

- 图片只接受 JPEG/PNG，每张不超过 15 MiB、25 MP；
- 见证照仍不超过 15 MiB，并额外限制为 16 MP；
- 视频不超过 500 MiB、600 秒、120 FPS；
- 视频全检最多分析 1,200 帧；
- 视频全检显式标记为高耗时手工测试，不进入页面自动冒烟，不自动重试；
- 代理按整个 multipart body 上限拒绝超量请求，不能依赖浏览器校验保证安全。

代理的精确 multipart 总上限与算法 ASGI 入口一致：单图 15,794,176 字节、两图
31,522,816 字节、参考图加视频 540,082,176 字节，均已包含 64 KiB multipart 元数据
余量。缺少长度、重复或冲突长度、负数以及超过上限均在读取请求体前拒绝。

代理上游读写总期限应比算法绝对截止时间多保留响应收尾余量：图像类 45 秒、视频快检
75 秒、视频全检 620 秒；上游连接最多 5 秒，请求头空闲最多 10 秒，客户端上传和上游
连续 15 秒无数据即中止。请求体发送完成后，等待同步算法的首个响应头或字节允许使用
该路由总期限的全部剩余时间，不设置 15 秒首字节超时；只有响应已经开始后，才对后续
响应分块应用 15 秒空闲限制。45/75/620 秒始终是不可突破的端到端最终上限。页面只允许
一次在途测试，重复点击被禁用。代理的唯一非阻塞并发槽只约束五条昂贵 POST，繁忙时
返回 429；静态资源、session、liveness 和 readiness 仍可响应。并发槽在 `finally` 中
释放。下游断连后代理不立即发起新请求，而是继续丢弃并排空当前上游响应，直至上游
完成或总期限到达。

## 响应展示

所有算法响应优先读取公共字段：

- `success`、`suggestion`、`reason_codes`；
- `model_versions`、`config_version`、`processing_time_ms`、`trace_id`。

能力特有展示：

- 白底照：模板版本、输出尺寸/格式、placement、matting quality 和派生图；
- 见证照：证卡区域数量、候选数量、保护区域比例、matting/blur 摘要和派生图；
- 两图比对：两侧质检摘要、`similarity_score`、校准状态、matcher 版本和固定
  `feature_dimension=1024`；
- 视频核验：mode、时长、帧汇总、聚合/中位/最低分、通过比例、主次轨迹比例、最长
  无人脸间隔和校准状态。

HTTP 200 只代表算法请求完成，也可能是 `RECAPTURE` 或 `REVIEW`。非 2xx 的错误体不
保证含 trace ID；页面需容忍未知 JSON 和非 JSON 错误，但只展示安全的状态码、原因码和
消息，不把服务端文本注入 HTML。

## 隐私与安全

### 浏览器内存

以下信息在浏览器侧只允许于当前标签页内存短暂存在：

- 原始照片、参考照、见证照和视频；
- 派生图片的 Base64、Blob 与 Object URL；
- 相似度、关键点、人脸框、姿态、证卡坐标、帧统计和 trace 信息。

不得写入 URL、Local/Session Storage、IndexedDB、Cache Storage、Service Worker、
埋点、错误上报或代码仓库。更换素材、重置和离开页面时必须清空引用，并对所有 Object
URL 调用 `URL.revokeObjectURL()`。用户主动点击下载是唯一持久化派生图的入口。

### 同源代理

允许的服务端路径只有：静态 `/`、`/index.html`、`/styles.css`、`/core.js`、`/app.js`，
测试台自身 `GET /health/live`，`GET /api/console/session`，精简
`GET /api/console/ready`，以及五条精确的 v2 POST。全部拒绝查询参数；不代理 legacy
`/api/extract_feature`、`/api/detect_face` 或任意 URL。

- 上游地址来自 `VISUAL_CONSOLE_UPSTREAM`，公共 origin 来自
  `VISUAL_CONSOLE_PUBLIC_ORIGIN`，均在启动阶段解析和固化，不能来自浏览器；
- 除容器内 liveness 特例外，所有请求的 Host 必须等于公共 origin 的 authority；顶层
  导航和同源 GET 通常不带 Origin，因此只校验 Host；
- 五条 POST 必须同时满足精确 Host、`Origin == VISUAL_CONSOLE_PUBLIC_ORIGIN`、合法
  multipart 和 `X-Visual-Console-CSRF`；
- `GET /api/console/session` 每次用 `secrets.token_urlsafe(32)` 生成内存态令牌，15 分钟
  过期，最多保存 256 个；每次创建和校验时清除过期项，达到容量时淘汰最早过期项，
  进程重启全部失效，不使用 Cookie；
- Docker 健康检查只允许来自容器 loopback 的 `GET /health/live`，可使用
  `Host: 127.0.0.1:8080`；该豁免不适用于静态页面、session、ready 或 POST；
- 请求头采用白名单，只向上游发送一个合法 `Content-Type`、一个合法
  `Content-Length`，并由代理生成 `Host` 与 `Connection: close`；拒绝所有
  `Transfer-Encoding`、`Expect`、重复长度、异常 boundary 和 hop-by-hop 组合；
- 响应头也采用白名单，只保留合法 `Content-Type` 和经过核对的 `Content-Length`，
  绝不转发 `Set-Cookie`、认证头、代理头或 hop-by-hop 头；无长度响应按实际字节计数；
- 派生图响应最多 22 MiB、两图响应最多 2 MiB、视频响应最多 4 MiB、readiness 最多
  1 MiB；超过限制立即关闭上游，向尚未开始的下游返回受控 502；
- 不记录请求体、文件名、响应媒体、Cookie、令牌、完整 trace 或上游内部异常；
- 所有页面、会话、API 和错误响应均设置 `Cache-Control: no-store`；
- CSP 固定为 `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:;
  media-src 'self' blob:; connect-src 'self'; font-src 'none'; object-src 'none'; base-uri
  'none'; frame-ancestors 'none'; form-action 'self'; worker-src 'none'; manifest-src 'none'`；
- 同时设置 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、
  `Referrer-Policy: no-referrer`、受限 `Permissions-Policy`，并只按固定扩展名返回固定 MIME；
- 不设置 CORS 响应头，不使用 SVG、外部字体、脚本、图片或 CDN。

测试台与当前 v2 API 都没有业务身份认证，只允许部署在已确认的可信内网地址。18003
和 18002 一样只绑定 `<server-37>`，不得绑定 `0.0.0.0` 或服务器第二块网卡。若后续
面向不可信网络，必须在测试台前新增 TLS、统一认证、来源 ACL、限流和审计，而不是把
本工具直接当生产门户。

## 测试策略

### 自动化测试

Python 标准库测试覆盖：

- 只允许预期静态文件，阻断目录穿越；
- Host、Origin、CSRF、方法和路径白名单；
- multipart `Content-Length` 缺失、重复、冲突、异常和超限，Transfer-Encoding、
  Expect、boundary 与 hop-by-hop 组合；
- Cookie、Authorization、Set-Cookie、内部令牌和代理头剥离；
- 请求与响应流式转发、慢速上传、上游超时、客户端断连、超大/无长度上游响应、429、
  错误透传、并发槽异常释放和 `no-store`；
- CSRF 过期、容量淘汰和进程重启失效；
- readiness 字段裁剪，不泄露模型路径或哈希。

Node 内置测试覆盖 `core.js`：

- PASS/RECAPTURE/REVIEW 的中文状态和颜色；
- HTTP 200 语义结果、未校准、活体未评估；
- 1024 维契约、分数/帧/派生图字段格式化；
- 原因码翻译、未知字段和恶意字符串按纯文本处理。

`core.js` 是浏览器和 Node 共用的 ESM；`package.json` 声明 `type=module`。固定命令
`npm run test:unit` 执行 Node 内置测试，`npm run test:e2e` 启动
`tests/mock_upstream.py` 和测试台后执行 Playwright。浏览器自动化验证五种表单切换、
拖放/选择、运行态、结果面板、派生图 Object URL 回收、全检提示和手机/桌面布局。
Playwright 只属于开发测试依赖，不进入 Docker 运行镜像。

### 真实算法冒烟

部署前后分别执行：

1. readiness 与五项能力展示；
2. 使用仓库内合成 fixture 验证稳定的错误/成功契约；
3. 在用户明确授权用于本次测试的 `download_images` 中用固定随机种子抽样，只用于异常
   处理、性能和“不崩溃”鲁棒性观察；随机样本不作为人脸准确率、同人/异人或见证照
   正确性的验收依据；
4. 使用同一张已授权图片做确定性的同图比对；不同随机图片只观察返回契约，不宣称
   两人身份必然不同；
5. 白底照和见证照的正向验收使用经过人工预检且用途适配的授权样本；
6. 使用合成视频执行 quick，full 仅手工执行一次短视频且不纳入默认冒烟；
7. 校验未标定时不出现身份 PASS，视频不出现“活体通过”；
8. 校验替换、重置和刷新后浏览器没有保留媒体和结果。

随机图片不复制进代码仓库、镜像或 fixture。默认只在终端显示随机种子、数量、建议/原因
码分布和耗时聚合，不持久化文件名、哈希或单图结果。若交付审计确需复现，由数据责任人
批准后把完整 SHA-256 与稳定样本 ID 写入受控审计位置，并约定保留期限和删除责任；哈希
不能被描述为匿名化身份信息。

## Docker 部署

测试台构建独立最小镜像 `face-algorithm-visual-console:task-2026-001`。镜像只包含
静态页面和标准库服务器，不包含模型、算法源码、配置、客户图片或令牌。

优先使用 bridge 网络并显式发布：

```text
<server-37>:18003 -> visual-console:8080
```

上游固定为 `http://<server-37>:18002`。2026-08-22 已从服务器现有 bridge 容器内
请求该地址的 `/health/live` 并得到 HTTP 200，因此当前拓扑不需要修改算法容器网络。

构建必须来自已记录 Git commit。构建后将 tag 解析成不可变 image ID，检查容器名、
端口和 cidfile 均未占用，再使用两阶段 `docker create`/`docker start`：

```bash
console_image_id=$(docker image inspect \
  --format '{{.Id}}' face-algorithm-visual-console:task-2026-001)

docker create \
  --cidfile /home/<user>/deployments/face-algorithm-task-2026-001/runtime/visual-console.cid \
  --name face-algorithm-visual-console-task-2026-001 \
  --user 10001:10001 \
  --workdir /app \
  --network bridge \
  --restart unless-stopped \
  --read-only \
  --cpus 0.50 \
  --memory 256m \
  --memory-swap 256m \
  --pids-limit 128 \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --log-driver json-file \
  --log-opt max-size=5m \
  --log-opt max-file=3 \
  --health-cmd "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2).read()\"" \
  --health-interval 30s \
  --health-timeout 3s \
  --health-start-period 5s \
  --health-retries 3 \
  --stop-timeout 10 \
  -p <server-37>:18003:8080 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e VISUAL_CONSOLE_HOST=0.0.0.0 \
  -e VISUAL_CONSOLE_PORT=8080 \
  -e VISUAL_CONSOLE_UPSTREAM=http://<server-37>:18002 \
  -e VISUAL_CONSOLE_PUBLIC_ORIGIN=http://<server-37>:18003 \
  "$console_image_id"

docker start "$(cat /home/<user>/deployments/face-algorithm-task-2026-001/runtime/visual-console.cid)"
```

记录构建 commit、image ID、container ID 和规范化 inspect。验收非 root、只读根、
cap-drop、NNP、CPU/内存/swap/PID、日志轮转、restart、healthcheck、唯一端口映射和完整
环境变量；重启该测试台容器后必须再次通过 LAN 页面和 readiness。`<server-37>` 当前是
DHCP/auto 动态地址，容器重启成功不证明主机重启或续租后地址不变。

## 验收与回滚

验收必须同时满足：

- `docker inspect` 证明 18003 只发布在 `<server-37>`；
- Windows 客户端 `<lan-host>` 能打开页面并读取 readiness；
- 五项页面输入、请求字段和结果组件符合公开 v2 契约；
- 随机图片、合成 quick 视频和一次短 full 视频完成预期语义验证；
- 页面源码和网络请求没有外部 CDN、任意上游、legacy token 或媒体持久化；
- 原 `face-algorithm:18001` 和候选 `face-algorithm-task-2026-001:18002` 的容器 ID、
  启动时间、重启次数、端口和健康状态未因测试台部署发生变化。

测试台是新增独立容器。回滚时只从受保护 cidfile 读取 container ID，并同时核对名称、
image ID 和创建时间；完全一致时才停止并删除该测试台容器。不得重建、停止或修改
18001/18002 算法容器。镜像、源码和审计材料默认保留；只有确认 image ID 无其他容器
引用且用户明确要求清理时才单独删除镜像。回滚后确认 18003 不再监听，同时重新验证
18001 和 18002 健康状态。

## 明确不做

- 不修改算法模型、特征维度、阈值、校准或 v2 响应；
- 不开放 embedding 或 legacy 特征接口；
- 不在测试台实现业务审批、合同状态、数据库、用户系统或客户证据存储；
- 不把 full 同步接口包装成正式生产队列；
- 不把当前内网测试台声明为公网或生产门户。
