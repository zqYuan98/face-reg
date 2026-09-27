# 人脸识别管理后台代码审查

> 这是修复前的历史审查快照，以下定位与行为对应 2026-09-22 原始代码。2026-09-24 的修复范围、实际验证及剩余边界见 [修复与验证记录](2026-09-24-backend-remediation-results.md)。

审查日期：2026-09-22。审查对象为当前工作目录 `face-reg-platform/backend-java`，并沿调用关系检查 Python 算法服务、Android 同步客户端及 Docker 配置。本次未修改业务代码，未连接配置中的业务数据库，未进行生产压测。

**结论：当前代码存在多条随人员数量、设备数量放大的无界工作链路，不建议直接用于十万人员规模的生产部署。没有分页的问题已经确认，而且只补管理页面分页仍不够。**

用户已澄清：导出功能应该尚未加入，主要关心管理表格是否全量加载。**答案是肯定的：人员列表和分组列表均未分页，人员列表还携带完整人脸特征，正常浏览即可触发大数据量开销。** 当前未发现 CSV、Excel、文件导出接口或按钮，以下以现有管理后台的问题为主，不将历史导出故障视为本版本已复现的事故。

| 已检查的页面/入口 | 当前实现 | 结论 |
|---|---|---|
| 人员管理 `/api/users/list` | 全量实体、完整特征、普通表格一次渲染 | 后端和前端均无分页，首要风险 |
| 分组管理 `/api/groups/list` | `selectList(null)`，完整数组交给普通表格 | 同样无分页，影响取决于分组总量 |
| 设备同步 `/api/sync/users` | SDK 强制拉取完整人员库 | 管理页分页不能覆盖此负载 |
| 管理页数量 `/api/users/count` | 数据库 COUNT | 不是全量实体加载，但仍需评估索引/频率 |
| 识别记录、导出 | 记录只有实体/Mapper/DDL，未发现业务查询和导出入口 | 不编造不存在的分页或导出实现 |

P1 表示应在生产大数据量使用前处理；P2 表示需要安排修复的性能、可靠性或数据一致性缺陷。缺陷存在不等于已经证明它就是历史事故的唯一原因。

## 1. [P1] 人员列表全量读取、返回完整特征，浏览器一次渲染全部人员

定位：[UserServiceImpl.java:106](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L106)、[UserController.java:35](../../backend-java/src/main/java/com/bonuos/face/controller/UserController.java#L35)、[User.java:18](../../backend-java/src/main/java/com/bonuos/face/entity/User.java#L18)、[index.html:425](../../backend-java/src/main/resources/static/index.html#L425)。

- `GET /api/users/list` 只有姓名和分组条件，没有页码、页大小或服务端上限。Service 用 `selectList` 读取全部匹配记录，按创建时间排序，没有字段投影。
- 列表直接返回 `User`，包括 `featureData`。页面只用该字符串判断“是否录入人脸”，却传输了整个特征向量。
- 前端 `fetch(...).then(r => r.json())` 解析完整响应后交给普通 `el-table`，每行还创建照片 `img`（231–263 行），没有分页或虚拟列表。
- 触发不需要导出：十万名有效人员时，打开管理页、搜索空条件、刷新即可走全量链路。数据库结果、Java 对象、人脸特征字符串、响应和浏览器对象都随总人数增长；照片请求进一步放大负载。不能把网络响应大小直接等同于 JVM 峰值堆大小，也不能把页面卡死和 Java 进程卡死混为一谈。

修复：数据库层真正分页，服务端校验页大小并设硬上限；列表使用窄 DTO，仅返回页面需要的字段和 `hasFeature`。排序增加稳定的 `id` 次序；深页/同步采用游标。前端同步改为只持有当前页、取消过期请求并按需加载缩略图。**只在浏览器对完整数组做切片不能解决后端问题。**

分组表也有同类缺口：[UserGroupServiceImpl.java:69](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserGroupServiceImpl.java#L69) 全量查询，[index.html:277](../../backend-java/src/main/resources/static/index.html#L277) 渲染全部分组。分组管理列表应分页；人员筛选/编辑用到的分组选项应采用有上限的远程搜索，不能直接用“当前分组页”代替完整可选范围。

## 2. [P1] 单个人员变更会触发在线设备全量同步

定位：[SyncServiceImpl.java:22](../../backend-java/src/main/java/com/bonuos/face/service/impl/SyncServiceImpl.java#L22)、[SyncManager.java:45](../../android-sdk/face-core/src/main/java/com/bonuos/facesdk/SyncManager.java#L45)、[SyncManager.java:179](../../android-sdk/face-core/src/main/java/com/bonuos/facesdk/SyncManager.java#L179)。

- 后端 `/api/sync/users` 同样返回完整 `List<User>`，无分页或上限；客户端明确不传 `lastSyncTime`，强制全量。
- 人员增、改、删都会广播 `UPDATE`。每台当时空闲的在线设备收到后会重新拉取全部人员，拼接完整响应字符串、解析全部 JSON，然后逐人请求照片并保存。
- N 个人员、D 台同时同步设备，一轮可产生 D 次 N 行读取与传输。只改一个人的姓名也会触发这一过程。照片即使未变化也重新请求；当前相对照片地址拼接还可能得到 404，失败请求本身仍有开销。

修复：全量初始化按稳定游标分块；后续使用持久化变更版本、删除墓碑和增量分页，限制并发并错峰重试。照片按版本/内容哈希判断变化。**不能直接让当前 SDK 使用增量或分页响应：它将返回的人员视为全集并删除本地缺失人员（194–195 行），会误删未包含在本批次中的用户。必须一起改协议和客户端。** 全量需一致快照或起始变更位点加后续追赶；所有页完整成功前不能执行缺失删除。

## 3. [P1] 批量删除逐条同步广播，慢设备会阻塞管理写请求

定位：[UserServiceImpl.java:197](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L197)、[SyncWebSocketHandler.java:29](../../backend-java/src/main/java/com/bonuos/face/handler/SyncWebSocketHandler.java#L29)。

- 批量删除没有数量上限，每个 ID 都执行查人、删除、广播；每次广播又在当前请求线程逐个 `sendMessage`。
- B 个删除目标、D 个在线连接最多产生 B×D 次发送。某个连接慢，会拖住当前删除请求及后续连接；空闲设备还会被反复唤醒全量同步。
- 客户端 `isSyncing` 会丢弃同步中的通知，因此不能声称每次通知一定对应一次全量查询；实际可能是重复查询，也可能是后面的变更完全没同步（见第 7 项）。

修复：批量规模有界，批次完成后合并发布事件；通知移出 HTTP 写请求，使用有界队列、每连接发送时限和缓冲上限，隔离慢连接。数据库提交后的通知失败必须能补发/补拉。

相关独立缺陷 **[P2] 并发发送不安全**：两个写请求可以同时对同一 `WebSocketSession` 发送，`CopyOnWriteArrayList` 只保护列表，不保护发送。仅捕获 `IOException`，运行时异常会中断广播，使数据库已成功而接口报错、后续设备收不到通知。应使用每会话串行发送或有界并发发送装饰器。[Spring 官方说明](https://docs.spring.io/spring-framework/docs/6.1.1/javadoc-api/org/springframework/web/socket/WebSocketSession.html#sendMessage(org.springframework.web.socket.WebSocketMessage))。

## 4. [P1] 人脸搜索每次重新加载整组特征并线性比较

定位：[UserServiceImpl.java:259](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L259)、[FaceUtils.java:28](../../backend-java/src/main/java/com/bonuos/face/util/FaceUtils.java#L28)。

`POST /api/users/search` 调算法后调用 `getUsers(null, groupId)`，把整组人员和特征读入内存并排序，再逐条将 JSON 解析为 `List<Float>`，计算余弦相似度。没有缓存、向量索引或并发隔离。同组十万人、每人 1024 维时，一次搜索需要遍历约 1.024 亿个维度，另有十万条 JSON 解析和大量对象分配；这是工作量推算，不是耗时实测。

修复：使用经过评估的向量索引/检索服务或可更新的紧凑向量缓存；设置搜索并发和资源预算。过渡方案可以有界分块扫描并维护全局最佳匹配，但不能只搜索列表第一页，否则会漏识别。

## 5. [P1] 算法调用没有超时，算法服务又在事件循环中执行同步推理

定位：[FaceFeatureExtractorClient.java:30](../../backend-java/src/main/java/com/bonuos/face/util/FaceFeatureExtractorClient.java#L30)、[FaceFeatureExtractorClient.java:85](../../backend-java/src/main/java/com/bonuos/face/util/FaceFeatureExtractorClient.java#L85)、[feature_server.py:81](../../FaceFeatureExtractorAPI/feature_server.py#L81)。

- Java 直接 `new RestTemplate()`，没有显式连接/读取时限；新增、换照、搜索均在请求线程同步等待 `postForEntity`。默认请求工厂继承系统超时设置，代码和容器启动参数没有提供有限的读取超时。当对端接受连接后一直不返回，默认环境可能持续占用请求线程。[Spring 超时文档](https://docs.spring.io/spring-framework/docs/6.1.1/javadoc-api/org/springframework/http/client/SimpleClientHttpRequestFactory.html#setReadTimeout(int))。
- Python `async def` 内直接同步解码、调用 `extract_features`；检测接口同样如此。部署脚本单进程启动 Uvicorn，推理期间事件循环不能继续服务其他请求，包括 `/health`。函数写成 async 不会自动把同步调用放进线程池。[FastAPI 官方说明](https://fastapi.tiangolo.com/async/#other-utility-functions)。
- 算法过载 → Java 等待累积 → 有限请求线程被占满，是独立于列表分页的后台失去响应路径。连接被立即拒绝与连接成功后挂起是两种情况，不能混为一谈。

修复：设置有限的连接、读取及总等待时间；算法访问加有界并发、过载拒绝和熔断。推理放到有界工作线程/进程并验证模型线程安全与内存预算，保留健康检查响应能力；不要简单创建无限线程。

## 6. [P1] 登录没有形成服务端访问控制

定位：[AuthController.java:19](../../backend-java/src/main/java/com/bonuos/face/controller/AuthController.java#L19)、[WebConfig.java:23](../../backend-java/src/main/java/com/bonuos/face/config/WebConfig.java#L23)、[index.html:353](../../backend-java/src/main/resources/static/index.html#L353)。

登录接口仅检查硬编码账户并返回随机 token；没有保存或验证 token 的代码，没有安全过滤器/认证拦截器。前端只判断 sessionStorage 中存在 token，后续业务 fetch 不携带 token。管理 API、同步 API 和上传照片映射均没有服务端鉴权。

任何能直连后端的人可以绕过页面登录，查询人员/特征/照片并调用管理接口，也能重复触发上述高成本操作。现网可能有外部网关或防火墙，本次未核实，不能把代码缺口理解为已确认公网可攻击。

修复：服务端统一身份认证和权限校验；管理员和设备身份分开，限制各设备能同步的范围；照片走鉴权下载或短期签名地址；移除硬编码账户，收紧 CORS/WebSocket 来源。用空数据库/MockMvc 验证匿名请求 401/403、合法角色有相应权限。

## 7. [P1] 同步期间丢弃通知，后台已删除人员仍可能留在设备

定位：[SyncManager.java:36](../../android-sdk/face-core/src/main/java/com/bonuos/facesdk/SyncManager.java#L36)、[SyncManager.java:83](../../android-sdk/face-core/src/main/java/com/bonuos/facesdk/SyncManager.java#L83)。

明确触发时序：设备读到包含 B 的全量快照 → 下载照片期间后台删除 B → UPDATE 到达时 `isSyncing=true`，直接 return → 设备保存旧快照并结束。没有 pending 标志、尾部再同步或周期补偿；`intervalSeconds` 参数没有使用，WebSocket 重连成功也只记日志。若后续没有新变更/人工同步/重启，B 可一直保留。数据量越大，这个窗口越长。同步 HTTP 只设置连接超时、没有读取超时，还可能使 isSyncing 长期不释放。

修复：同步期间通知记录为待处理，结束后追到目标版本；每次建连/重连补拉，低频核对版本；使用线程安全状态和读取时限。结合第 2 项的版本/墓碑协议一起修复。

## 8. [P2] 强制逐行 SQL 标准输出放大全量查询成本

定位：[application.yml:22](../../backend-java/src/main/resources/application.yml#L22)。

配置 `org.apache.ibatis.logging.stdout.StdOutImpl`，debug/trace 始终启用；结果读取对每行同步输出日志。十万条结果产生约十万条 Row 日志，多设备全量同步会重复产生。字符串拼接、标准输出和容器日志写入发生在查询链路上。仅改 `logging.level=INFO` 不能关闭这一实现。[StdOutImpl 源码](https://mybatis.org/mybatis-3/xref/org/apache/ibatis/logging/stdout/StdOutImpl.html)、[ResultSetLogger 源码](https://mybatis.org/mybatis-3/xref/org/apache/ibatis/logging/jdbc/ResultSetLogger.html)。

TEXT 在结果日志中可能显示为 `<<BLOB>>`，本报告不声称 SELECT 必然打印完整特征，但逐行日志本身已经有明显放大效应。修复为 SLF4J 配合关闭结果 TRACE、慢查询和聚合指标，并配置日志轮转/容量。

## 9. [P2] 批量删除失败被吞掉，接口仍返回全部成功

定位：[UserServiceImpl.java:197](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L197)、[UserController.java:90](../../backend-java/src/main/java/com/bonuos/face/controller/UserController.java#L90)。

每个 ID 的异常只输出日志并继续，Controller 无条件返回 `code=200` 和“批量删除成功”。数据库超时、某条删除失败时，页面无法知道哪些人员仍保留；广播异常也可能出现“数据删了但被当成单条失败”的混合状态。

修复：明确批次语义，返回实际成功/失败数量及失败 ID/原因；需要全部成功语义时采用有界批次事务，通知在提交后执行。异常应反映到正确 HTTP 状态和监控指标，不能用一条成功消息掩盖部分失败。

## 10. [P2] 失败上传和照片替换会持续留下孤儿文件

定位：[UserServiceImpl.java:54](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L54)、[UserServiceImpl.java:159](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L159)。

新增先落盘，再提取特征/写数据库；后两步失败时不清理文件。换照失败遗留新照片，成功则覆盖数据库路径而不清理旧照片。搜索有 finally 清理，但新增/更新没有。单文件 10 MB 限制不能控制累计占用，算法故障时反复重试会加速填满持久化上传目录。

修复：暂存、验证、提交引用、清理旧文件的明确流程；失败清理和可重试回收任务，统计孤儿文件与磁盘余量。数据库事务不能回滚文件写入。

## 11. [P2] 新增/编辑成功会重复请求两遍人员列表和数量

定位：[index.html:478](../../backend-java/src/main/resources/static/index.html#L478)、[index.html:584](../../backend-java/src/main/resources/static/index.html#L584)。

`submitUser` 成功后调用 `loadUsers()`、`loadUserCount()`，又调用包含这两个请求的 `loadData()`，导致一次操作实际执行两次全量列表请求和两次数量查询。它也没有请求取消或结果版本保护。

修复：只保留一条刷新链，必要时合并/取消重复请求；列表改为分页后只刷新当前页和需要更新的统计。已通过提取实际内联脚本的 Node 隔离执行验证此重复请求。

## 12. [P2] 声明的表结构缺少主要列表/增量查询所需联合索引

定位：[schema.sql:12](../../backend-java/src/main/resources/db/schema.sql#L12)、[UserServiceImpl.java:109](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L109)。

当前建表只有主键、user_no 唯一约束和 group_id 外键；InnoDB 会为外键提供相应索引，因此不能称“group_id 完全没有索引”。但没有匹配 `status + create_time + id`、`group_id + status + create_time + id` 的联合索引，也没有 update_time 游标索引。姓名查询使用 `%关键词%`，不能期望普通姓名 B-tree 索引解决包含搜索。分页只能限制返回行数，不能保证每次查询只扫描少量记录。

修复：结合真实筛选比例与执行计划评估上述候选联合索引、索引体积及写入成本；深页使用稳定游标，包含搜索单独设计。当前未连接数据库，实际索引可能有额外运维变更，也未执行 MySQL EXPLAIN，不能给出现场扫描行数。

## 13. [P1，默认部署条件] 数据库发布到宿主网络并使用固定 root 凭据

定位：[docker-compose.yml:9](../../docker-compose.yml#L9)、[docker-compose.yml:50](../../docker-compose.yml#L50)、[application.yml:7](../../backend-java/src/main/resources/application.yml#L7)。

Compose 发布 MySQL `13307:3306`，未限定回环地址；初始化凭据写死，后台直接使用 root。另一套数据库凭据也明文保存在 application.yml（这里不重复口令）。实际可达性取决于宿主防火墙、历史数据卷和账号授权，本次未实连核实。[Docker 默认端口发布行为](https://docs.docker.com/engine/network/port-publishing/)。

修复：数据库保持在服务内部网络，运维访问走受控通道；应用使用最小权限账号；密钥注入并轮换已暴露凭据。移除配置文件中的密码不能代替轮换。

## 14. [P2] 同组重名约束和设备注册依赖“先查后写”，并发下不成立

定位：[UserServiceImpl.java:303](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L303)、[DeviceServiceImpl.java:20](../../backend-java/src/main/java/com/bonuos/face/service/impl/DeviceServiceImpl.java#L20)。

同组同名先 `selectCount`，随后才执行耗时特征提取并 insert，数据库没有相应业务唯一约束；两个请求可同时通过检查并都落库。设备首次注册也先查后插，虽然 device_sn 有唯一约束，但第二个请求会报错，未处理为幂等成功。

修复：明确未分组/禁用用户语义后用数据库唯一约束保证人员规则；设备使用原子 upsert 或冲突后读取/更新。普通事务或全局 Java 锁不能作为通用替代方案。

## 15. [P2] 增量同步接口的删除和水位语义不完整

定位：[SyncController.java:21](../../backend-java/src/main/java/com/bonuos/face/controller/SyncController.java#L21)、[SyncServiceImpl.java:26](../../backend-java/src/main/java/com/bonuos/face/service/impl/SyncServiceImpl.java#L26)、[UserServiceImpl.java:215](../../backend-java/src/main/java/com/bonuos/face/service/impl/UserServiceImpl.java#L215)。

接口支持 lastSyncTime，但删除是硬删除，没有墓碑，增量客户端无法知道谁已被删；下一次水位用查询结束后的当前时间，查询快照与响应时间之间的修改可能被跨过，秒精度 DATETIME 与毫秒水位也存在边界问题。当前仓库 SDK 实际强制全量，这项不能当作它已经在使用增量的证据，但它是后续性能改造必须修复的协议缺陷。

修复：以单调持久化变更序列和删除墓碑作为游标，定义快照与增量衔接、分页完成与水位确认。水位必须对应安全的已提交顺序/可恢复变更日志，不能直接把普通 AUTO_INCREMENT ID 当作提交顺序，因为并发事务可能先分号、后提交。墓碑需保留至客户端确认，过期客户端则走完整重建。不要直接把“查询结束时间”当作“已完整处理的数据版本”。

## 验证与证据

- 原始 WebSocket handler 加最小类型桩，验证慢会话阻塞调用线程、后续会话等待、并发发送异常向外传播及中断后续广播，4 项控制流断言通过。[脚本](evidence/backend-remediation/verification-summary.json)、[结果](evidence/backend-remediation/verification-summary.json)。这不是 Tomcat 网络压测。
- 提取原始 Python endpoint 函数，用两次各 200 ms 的同步推理替身测试：整体 401.85 ms，健康检查协程到 401.81 ms 才开始运行，确认事件循环阻塞路径。[脚本](evidence/backend-remediation/verification-summary.json)、[结果](evidence/backend-remediation/verification-summary.json)。这不是实际模型耗时测量。
- 提取原始管理页 JS，在 Node 中替换 Vue/fetch：验证首次列表无分页参数、编辑成功后两次列表及两次数量请求。[脚本](evidence/backend-remediation/verification-summary.json)、[结果](evidence/backend-remediation/verification-summary.json)。没有执行真实页面渲染性能测试。
- Java 隔离副本使用原 pom 和主源码构建成功：**6 项 JUnit 测试，0 failures、0 errors、0 skipped**。验证实际 QueryWrapper 无 LIMIT/字段投影、逐条删除广播、批量失败仍报成功、实际 User/Jackson 序列化、匿名 MVC 请求成功、实际 MyBatis 逐行 stdout。业务服务/Mapper 或网络依赖按测试目标替换为 mock，未连接数据库。测试通过表示确认缺陷行为，不表示已修复。[测试日志](evidence/backend-remediation/verification-summary.json)、[测试源码](evidence/backend-remediation/verification-summary.json)。
- 没有现场 heap dump、GC/线程栈、慢查询和容器资源指标，未在实际部署中压测卡死阈值。隔离副本的 34 个主文件及 pom 与源文件哈希一致，业务代码未改动。

### 合成数据体积测算

使用固定种子 20260922 生成 100 个归一化 1024 维 float 向量，按照业务相同的 `Float.toString` 方式组成特征 JSON 字符串，交给实际 `User` 和 Jackson 序列化。仅构造小样本，未读取真实人员或构造十万条大对象。

| 项目 | 结果 |
|---|---:|
| 每人 featureData 平均 UTF-8 字节 | 12,130.30 |
| 每人完整 User JSON 平均字节 | 12,376.30 |
| 同类分布十万人的 JSON 数组体积估算 | 1,237,730,001 字节 |
| 换算 | 约 1.24 GB / 1.15 GiB，未压缩 |

这说明问题不是一个轻量姓名表格：合成样本约 98% 的 JSON 字节来自特征字符串。该估算不是实际数据库精确体积、不是所有特征的数学下界、不是 JVM 峰值堆内存，也不是在线压测结果；照片和渲染开销未计入。响应压缩不能消除全量对象及解析后的内存开销。

## 修复顺序与验收建议

1. **先消除无界请求**：数据库分页 + 窄字段 DTO；统一鉴权；算法超时与并发上限；关闭逐行 SQL 日志；批量操作限制规模并合并广播。
2. **同时治理后台隐性负载**：同步协议与 SDK 一起改为分块/增量/墓碑，通知合并和慢连接隔离；搜索改用可控向量检索。不能仅修改管理页就宣布大数据问题已解决。
3. **补可靠性**：部分失败结果、照片回收、幂等写入、合适索引、日志与指标、密钥和数据库网络配置。
4. **若以后增加大批量导出**：使用有界后台任务、数据库分块读取和流式写文件，不复用当前无界列表接口。

建议在隔离预发库准备 1 万/10 万/更大规模的合成用户及真实维度特征，分别验证：分页、条件搜索、首轮同步、多设备同时同步、批量删除、算法长延迟、慢 WebSocket，以及后台任务期间正常管理请求。记录 p95/p99、JVM 堆/GC、线程占用、DB 扫描/慢 SQL、响应字节、日志量和磁盘增长；依据实际硬件与业务要求设验收阈值，不承诺未经测试的 QPS 或秒数。

专项正确性验收：同步中删除和断线重连后必须补齐；分页不能误删其他页人员；批量部分失败不能报全成功；孤儿文件可回收；匿名请求被拒绝；列表/同步规模增长时峰值内存应由批次和并发上限约束。
