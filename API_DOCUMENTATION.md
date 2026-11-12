# 人脸识别系统 API 接口文档

更新日期：2026-09-24。本文对应当前源码，基础路径为同源 HTTPS 站点的 `/api`；设备同步、管理会话和内部算法接口分别说明。**当前版本没有导出接口或导出按钮；本次没有新增 CSV/Excel 十万行导出功能。**

## 1. 响应与身份

Java 普通成功响应为 `{"code":200,"msg":"success","data":...}`；部分删除/退出等接口没有 data。错误使用实际 HTTP 状态及 `{"code":状态码,"errorCode":"稳定错误码","msg":"可展示说明"}`，不能只检查 HTTP200 或只把数组是否为空当结果。批量删除另有 HTTP207/422 的逐项结果。

管理 API（含旧列表）使用服务器会话；设备使用独立 Bearer 凭据。两者不能互相替代。公开页面 `/login.html`、登录所需图标及 `/api/csrf` 不等于开放管理数据。生产 Cookie 为 HttpOnly、SameSite=Lax、Secure，默认空闲 30 分钟失效。

| 接口 | 输入/行为 | 成功 data |
|---|---|---|
| `GET /api/csrf` | 登录前可访问，浏览器保留同源 Cookie | `{token,headerName:"X-CSRF-TOKEN"}` |
| `POST /api/login` | JSON `{username,password}`，附上述 CSRF 请求头 | `{username}`；设置会话，不返回伪 token |
| `GET /api/me` | 已登录管理员 | `{username}` |
| `POST /api/logout` | 管理会话及 CSRF | HTTP200，注销会话，无 data |

登录会更换会话 ID 并作废旧 CSRF，成功后再次 GET `/api/csrf`。全部管理 POST/DELETE 等写请求附新 `X-CSRF-TOKEN`；前端 fetch 使用同源 credentials。收到 401 回到登录，403 `CSRF_INVALID` 重新获取安全令牌后由用户确认重试，不能无限自动重试有副作用的写请求。

完整配置见 [安全迁移说明](backend-java/SECURITY_MIGRATION.md)。

## 2. 管理人员分页与分组

### 人员列表

`GET /api/users/page?page=1&size=20&name=张&groupId=7`

| 参数 | 约束 |
|---|---|
| `page` | 默认 1，正整数 |
| `size` | 默认 20，1–100；界面提供 20/50/100 |
| `name` | 可选；trim 后最多 100 字，按字面做包含匹配，`%`、`_`、`!` 不作为用户通配符 |
| `groupId` | 可选正整数；省略时不按组过滤 |

offset `(page-1)*size` 最大 1000000，溢出或超限 HTTP400。按 `createTime DESC,id DESC` 排序；数据库先分页 ID，再读取当前页的展示明细，同一只读快照中计数，不会先加载所有人员。

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "records": [{
      "id": 123,
      "userNo": "example-user-number",
      "name": "示例人员",
      "groupId": 7,
      "groupName": "示例分组",
      "photoUrl": "example.jpg",
      "hasFeature": true,
      "status": 1,
      "createTime": "2026-09-24T09:00:00",
      "updateTime": "2026-09-24T09:00:00"
    }],
    "total": 100000,
    "page": 1,
    "size": 20
  }
}
```

分页响应没有 `featureData`。`hasFeature` 沿用数据库特征字符串长度大于 20 的展示规则，不是重新识别或模型质量评分。`GET /api/users/count` 的 data 是全局 `status=1` 人员数量，不是当前页人数或当前筛选总数。

### 分组列表、选项和计数

| 接口 | 参数 | data |
|---|---|---|
| `GET /api/groups/page` | `page=1,size=20,keyword?`；页边界同人员，keyword trim 后最多100字，按分组名称字面包含匹配 | `{records,total,page,size}`，按 `id DESC` |
| `GET /api/groups/options` | `keyword?`, `afterId=0`（非负），`limit=20`（1–50） | `{items:[{id,groupName,groupNo}],nextCursor,hasMore}`，按 `id ASC` |
| `GET /api/groups/count` | 无 | 分组总数 |
| `GET /api/groups/{id}` | 分组 ID | 分组详情；不存在 HTTP404 |

options 返回最后一页时 `hasMore=false,nextCursor=null`；有下一页时将 nextCursor 作为下次 afterId。编辑已有人员可按分组 ID 取详情回填，不能为一个下拉框加载全部分组。

**分组字段叫 `groupName`，不是 `name`。** 分组对象字段为 `id,groupNo,groupName,description,createTime,updateTime`。

| 接口 | JSON 输入 | 行为 |
|---|---|---|
| `POST /api/groups/add` | `{groupName,description?}` | 名称 trim 后1–100字，描述最多255字；id/groupNo/时间由服务器生成，data 为新分组 |
| `POST /api/groups/update` | `{id,groupName,description?}` | 修改名称和描述；data 回显提交的分组对象，需完整服务端对象时再 GET 详情 |
| `DELETE /api/groups/{id}` | 无 body | 有任何人员仍关联时 HTTP409 `GROUP_NOT_EMPTY`；不会级联删除或自动移组；成功无 data |

清空已有描述传空字符串；当前 MyBatis 的非空字段更新策略不会通过 `description:null` 清空旧值。

### 旧管理列表过渡

`GET /api/users/list?name=...&groupId=...` 和 `GET /api/groups/list` 仅用于旧调用方过渡，仍需管理员会话。SQL 最多预读 101 个 ID：结果不超过 100 时返回完整旧 data 数组；超过 100 时 HTTP409 `PAGINATION_REQUIRED`，不返回部分数组。人员旧数组仍含完整 User 字段，不能作为新管理表格的数据源。

此限制不应用于设备 `/api/sync/users`；设备旧同步必须保持完整名单契约。

## 3. 人员增改、详情和删除

### 增改与详情

| 接口 | 输入 | 成功 data |
|---|---|---|
| `POST /api/users/add` | multipart：`name`必填、`groupId`可选、`photo`必填 | 完整 User |
| `POST /api/users/update/{id}` | multipart：`name`必填、`groupId`可选、`photo`可选 | 完整 User |
| `GET /api/users/{id}` | 人员 ID | 完整 User；不存在 HTTP404 |
| `DELETE /api/users/{id}` | 正整数 ID | 成功无 data；已不存在按幂等成功处理 |

姓名 trim 后1–100字。指定 groupId 必须存在且为正整数；同一分组的活跃人员不能重名，未分组人员也共同检查重名，冲突 HTTP409。上传请求与单张图片默认上限 10 MiB，算法须得到有效的1024维非零有限数值特征后才写入人员记录。

**编辑接口的 groupId 是目标分组：省略/空值表示移为未分组，不是“保持原组”。** 保持原组需显式提交原 groupId。photo 省略或空文件则保留现有照片；上传新照片成功引用后安排旧照片回收。完整 User 含 `id,userNo,name,groupId,featureData,photoUrl,status,createTime,updateTime,syncVersion`；其中 featureData 是序列化特征字符串。只有分页 DTO 保证不返回特征，客户端不要记录完整 User 响应到日志。

照片地址使用同源 `/uploads/{单个编码后的文件名}`，需要管理员会话或获准的设备身份。历史中文、空格、字面 `+` 可作为一个 UTF-8 路径段编码；不能传隐藏目录、跨目录、双重编码或含冒号/控制字符的路径。不得把整个任意远端 URL 直接用作照片来源。

### 批量删除

`POST /api/users/deleteBatch`，Content-Type `application/json`，body 为正整数 ID 数组。输入原始长度1–100，分数 ID（如1.9）拒绝；执行前校验并去重。只有本批实际删除了人员才发一次同步通知。

| HTTP/code | 语义 |
|---|---|
| 200 | 全部去重目标成功，包括已不存在 |
| 207 | 部分成功、部分失败 |
| 422 | 所有去重目标均失败，仍返回逐项结果 |
| 400/401/403 等 | 参数/认证/整体请求被拒绝，不属于部分执行结果 |

示例输入 `[11,12,13,11]` 的部分成功响应：

```json
{
  "code": 207,
  "msg": "部分人员处理失败",
  "data": {
    "requestedCount": 4,
    "uniqueCount": 3,
    "successfulIds": [11,12],
    "alreadyAbsentIds": [12],
    "deletedCount": 1,
    "failures": [{"id":13,"errorCode":"DELETE_FAILED","message":"删除失败，请刷新后重试"}],
    "successCount": 2,
    "failureCount": 1
  }
}
```

`successCount+failureCount=uniqueCount`；successfulIds 包含已不存在的目标，deletedCount 只统计实际删除。数据库资源故障会终止后续尝试，未执行项标记 `NOT_ATTEMPTED`；发生连接异常的项目可能是 `DATABASE_UNAVAILABLE`、结果未确认，应刷新核对后仅重试失败项。照片回收或通知失败不能把已提交的删除改报成未删除。

## 4. 人脸搜索

`POST /api/users/search`，multipart：`photo` 与正整数 `groupId` 必填。服务先提取特征，再对该组全部活跃人员做精确比对（O(N×D)），维护整个搜索范围的第一名和第二名；不复用管理表格当前页。

- 比对优先走内存特征库：每个分组一份预归一化的特征矩阵，按 `sync_revision` 判断新旧，落后时只重放 `user_change_log` 中涉及该组的人员；首次访问、epoch 变化、日志被裁剪或积压超过 `face.search.gallery.max-delta`（默认 2000）时在后台重建，重建完成前按 ID 游标分块读库比对，结果相同。超出 `face.search.gallery.max-bytes`（默认 128 MiB，1024 维约 3 万人）的分组不缓存，始终读库比对。
- 检索使用的特征版本由 `face.search.feature-version` 决定（环境变量 `FACE_SEARCH_FEATURE_VERSION`）：默认 `recognizer_v1_1024`，可选 `arcface_r50_512`，见 4.1。探针照片按同一版本提取。
- 相似度阈值：v1 用 `face.search.threshold`，默认 0.6；v2 用 `face.search.v2-threshold`，默认 0.45（LFW 上 FAR 1e-5 约对应 0.34、1e-6 约对应 0.63）。两者都未校准；`face.search.margin` 大于 0 时，第一名领先第二名不足该值返回 404 `AMBIGUOUS_MATCH`，默认 0 即关闭。
- 并发上限 `face.search.concurrency` 默认 4，超出时在搜索预算内排队。

- 找到匹配：HTTP200，data 为 User，不含 `featureData`。
- 完成全部比对且无匹配：HTTP404 `NO_MATCH`；第一名与第二名过近：HTTP404 `AMBIGUOUS_MATCH`。
- 搜索预算耗尽：HTTP503 `SEARCH_INCOMPLETE`，代表未完成，不能显示为“没有此人”。
- 搜索期间数据版本变化：HTTP409 `SEARCH_CHANGED`，需重新搜索。
- 资源繁忙（排队超时）：HTTP429 `SEARCH_BUSY` 或 `ALGORITHM_BUSY`。

本机实测内存扫描（1024 维，单线程）：1 万人约 2 ms，10 万人约 25 ms。v2 为 512 维，耗时约减半，同样的内存预算能缓存的人数约翻倍。

直接改库（绕过本服务的人员增改删）不会写变更日志，已缓存的分组看不到这些改动；改完后需重启后端，或等该分组下次重建。

### 4.1 特征版本 arcface_r50_512

- **定位**：第二个特征版本，与 v1 并存。在 LFW 上 TAR@FAR=1e-5 为 99.86%，v1 为 88.32%。使用 InsightFace 权重，仅限非商业研究用途。算法侧说明见 [FEATURE_V2.md](FaceFeatureExtractorAPI/FEATURE_V2.md)。
- **注册**：`face.features.versions`（环境变量 `FACE_FEATURES_VERSIONS`，逗号分隔）列出注册时要提取的版本。每个版本都必须提取成功，否则注册失败并返回 422 `NO_USABLE_FACE`，`msg` 给出具体原因（人脸过小、角度过大、过暗、过亮、模糊）。
  - v1 特征照旧写入 `sys_user.feature_data`，同步给设备。
  - 其他版本的特征写入 `sys_user_feature(user_id, feature_version, feature_dim, feature_blob, photo_url)`。`feature_blob` 为 little-endian float32；`photo_url` 记录特征来自哪张照片。
  - 写入与人员记录、变更日志在同一事务里。换照片时旧照片的特征一并删除；删除人员时级联删除。
- **约束**：检索版本必须包含在注册版本中，否则后端拒绝启动。
- **上线步骤**：
  1. 执行迁移 `db/migrations/V20261016_01__user_feature.sql`。
  2. 算法服务设 `FACE_FEATURE_VERSIONS=recognizer_v1_1024,arcface_r50_512` 并挂载权重。
  3. 后端设 `FACE_FEATURES_VERSIONS=recognizer_v1_1024,arcface_r50_512`，检索版本暂时保持 v1。
  4. 调用补提接口，为存量人员提取 v2 特征。
  5. 状态接口显示 `current == enrollable`（失败人员已处理）后，把 `FACE_SEARCH_FEATURE_VERSION` 改为 `arcface_r50_512` 并重启。
  6. 回滚：把检索版本改回 v1 即可，v1 数据始终完整。
- **设备同步不变**：仍只下发 v1 特征。设备端 InsightFace 引擎继续用同步下来的注册照自行建库。

| 管理接口（管理员 Session + CSRF） | 说明 |
|---|---|
| `GET /api/features/status?version=arcface_r50_512` | `searchVersion`，以及 `backfill`：`enrollable`（有照片的在职人员数）、`current`（已有该照片对应特征的人数）、`running`、`processed`、`written`、`failed`、`recentFailures`（最近 50 条 `{userId, reason}`） |
| `POST /api/features/backfill?version=arcface_r50_512` | 启动补提，返回 202；已在运行时返回 409 `BACKFILL_RUNNING`；v1 返回 400 |
| `POST /api/features/backfill/stop` | 请求停止，当前这批处理完后退出 |

**补提的行为**：
- 按人员 ID 顺序走一遍，读取库中存储的注册照提取特征，并发数为 `face.features.backfill-concurrency`（默认 2）。
- 失败的人员保持缺失，并给出原因：`PHOTO_MISSING`、`NO_USABLE_FACE` 等。重新启动补提会再次尝试它们，也就是从数据实际缺的地方续跑。
- 只有当人员仍是同一张照片时才写入。补提期间有人重新注册，以新注册的结果为准。

## 5. 设备注册、同步和通知

设备发送 `Authorization: Bearer deviceSn.secret`；服务器按配置中的完整 token SHA-256、SN、组范围和 scopeVersion 授权。注册/同步/照片请求均使用该身份。管理员 Cookie 不能调用设备接口，设备 token 不能调用管理接口。旧无凭据设备只可能通过默认关闭、带截止时间和精确 IP/SN 库存的专用兼容通道。

`POST /api/devices/register` 的参数是表单/查询参数 `deviceSn`（必填，最长64字符）和 `ip`（可选，最长50字符），不是 JSON body。deviceSn 必须等于认证身份；ip 只是上报字段，不作为安全身份来源。成功 data 为设备记录；相同 SN 原子注册/更新心跳。

| 接口 | 契约 |
|---|---|
| `GET /api/sync/users?lastSyncTime=...` | v1 完整 data 数组；lastSyncTime 为兼容保留并忽略，不做增量/分页。响应还含 timestamp、epoch、revision。只允许全库设备身份或获准旧通道。 |
| `GET /api/sync/v2/state` | 当前 epoch/head/retentionFloor/scopeVersion |
| `POST /api/sync/v2/bootstrap` | 建立初始化租约，取得起点L和最大ID M |
| `GET /api/sync/v2/bootstrap/{leaseId}/page` | `afterId=0,limit=200`，limit为1–200；初始化分块 |
| `POST /api/sync/v2/bootstrap/{leaseId}/finish` | 完成扫描后固定追赶上界U |
| `GET /api/sync/v2/changes` | epoch、scopeVersion、afterRevision、untilRevision，limit默认200，可带leaseId |
| `POST /api/sync/v2/ack` | JSON确认epoch/scopeVersion/revision及可选leaseId |
| `GET /ws/sync` WebSocket握手 | 携带设备凭据，接收文本 `UPDATE`；通知仅提示追赶，不是持久事件本身 |

v1 返回完整文件，含 `Content-Length`、`X-Sync-Revision`、`X-Snapshot-Created`；忙时429 `SYNC_BUSY`，生成失败503 `SNAPSHOT_UNAVAILABLE`。下载中断或JSON不完整属于失败，设备不得用部分/空数组替换本地库。服务端分块写磁盘不等于旧 SDK 已能在设备内存中解析任意大数组。

v2 的 epoch、授权版本、历史窗口或租约失效返回409 `SNAPSHOT_REQUIRED`。客户端必须保留旧在线库，构建 staging、重放变更和照片全部成功后原子发布；按 nextRevision 前进，不能把“本页无可见事件”当作全库为空。完整字段及状态机以 [v2 线协议](docs/superpowers/specs/2026-09-24-sync-v2-wire-contract.md) 和 [SDK 接入文档](android-sdk/SYNC_V2.md) 为准。

## 6. Java 常见错误码

下表列出客户端应区分的主要分支，不代表所有校验的穷举。以实际 HTTP 状态和 errorCode 共同判断。

| HTTP | 主要 errorCode | 客户端处理 |
|---|---|---|
| 400 | `INVALID_ARGUMENT`, `INVALID_REQUEST`, `INVALID_BATCH`, `INVALID_NAME`, `INVALID_GROUP`, `INVALID_ID`, `INVALID_DEVICE` | 修正输入；不要退化成全量请求 |
| 401 | `UNAUTHENTICATED`, `BAD_CREDENTIALS`, `DEVICE_UNAUTHORIZED` | 管理重新登录或修复设备凭据 |
| 403 | `CSRF_INVALID`, `FORBIDDEN`, `DEVICE_SCOPE_DENIED` | 检查CSRF/权限/设备SN；不能匿名降级 |
| 404 | `NOT_FOUND`, `USER_NOT_FOUND`, `GROUP_NOT_FOUND`, `NO_MATCH` | 区分资源不存在和已完成搜索无匹配 |
| 409 | `PAGINATION_REQUIRED`, `DUPLICATE_NAME`, `DUPLICATE_RECORD`, `GROUP_NOT_EMPTY`, `SEARCH_CHANGED`, `SNAPSHOT_REQUIRED` | 使用分页、解决冲突、重新搜索或完整初始化 |
| 413 | `UPLOAD_TOO_LARGE`, `IMAGE_TOO_LARGE` | 缩小上传图片/请求 |
| 422 | `NO_USABLE_FACE`, `INVALID_IMAGE` | 更换符合质量要求的人脸照片；批量删除的422则读取data.failures |
| 429 | `ALGORITHM_BUSY`, `SEARCH_BUSY`, `SYNC_BUSY`, `LOGIN_BUSY`, `LOGIN_RATE_LIMIT` | 有限退避，避免叠加请求 |
| 502 | `ALGORITHM_INVALID_RESPONSE`, `INVALID_FEATURE`, `EXTRACTION_FAILED` | 算法响应错误；记录错误码并排查依赖 |
| 503 | `DATABASE_UNAVAILABLE`, `ALGORITHM_UNAVAILABLE`, `ALGORITHM_CANCELLED`, `SEARCH_INCOMPLETE`, `SNAPSHOT_UNAVAILABLE`, `ADMIN_NOT_CONFIGURED` | 操作未正常完成；不当作空数据或无匹配 |
| 504 | `ALGORITHM_TIMEOUT` | 算法交换超时，操作未完成 |
| 500 | `INTERNAL_ERROR` | 服务端内部错误；不依赖异常栈或SQL细节 |

## 7. 内部 Python 算法服务

算法服务只供 Java 内网调用，没有管理员 Session/CSRF 包络，也不应作为匿名公网人脸提取服务。默认端口8000，Compose宿主只绑定本机18000。

| 接口 | 输入/输出 |
|---|---|
| `GET /health` | 事件循环存活检查：`{"status":"healthy","service":"Face Feature Extractor"}`，绕过推理槽 |
| `GET /ready` | 模型已就绪时200，初始化/失败/长时间占槽时503；需检查状态及 restart_required |
| `POST /api/extract_feature` | multipart的 `image`；成功为 `{success:true,message,feature:[1024维数值],feature_dim:1024,processing_time}` |
| `POST /api/detect_face` | multipart的 `image`、可选 `expand_scale=0.0`（有限数值0–10）；返回success/message/faces/processing_time |
| `POST /api/v2/extract_feature` | multipart的 `image`、可选 `quality_filter`；arcface_r50_512 特征、人脸框、关键点、姿态、质量与 `reason_code`。仅在 `FACE_FEATURE_VERSIONS` 含该版本时可用，见 [FEATURE_V2.md](FaceFeatureExtractorAPI/FEATURE_V2.md) |
| `GET /api/feature_versions` | 本进程已加载的特征版本 |
| `POST /api/compare_feature` | JSON 两个同版本特征的 1:1 余弦分数，见 [FEATURE_COMPARISON.md](FaceFeatureExtractorAPI/FEATURE_COMPARISON.md) |
| `POST /api/search_feature` | JSON 查询特征与 1–1000 个调用方候选的 1:N 排名（top_k），独立工作槽、忙时 429，见同上 |

faces 每项为 `x1,y1,x2,y2,score`，坐标为像素，扩展后裁剪到图片边界。提取未得到可用品质人脸时可能 HTTP200、`success:false`；Java 将其映射为422 `NO_USABLE_FACE`，不要把 Python 的200当作一定取得特征。Python错误通常是 `{"detail":{"errorCode":"..."}}`，输入验证也可能使用 FastAPI 的标准 detail 数组，不能套用Java包络。

各推理接口共用一个原生工作槽，槽忙时在超时内有界排队（`FACE_INFERENCE_QUEUE`）；按进程扩容（`FACE_WORKERS`），CPU 与 GPU 两个部署版本见 FEATURE_V2.md。请求超时/取消不表示底层原生推理已经停止；槽位保留到真实任务结束，长期挂起由就绪检查降级和部署监督重启处理。具体预算、错误和验证边界见 [算法资源限制说明](FaceFeatureExtractorAPI/BOUNDED_EXECUTION.md)。
