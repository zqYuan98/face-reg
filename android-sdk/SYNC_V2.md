# 同步 SDK v2 接入与验证

本页适用于当前源码。仓库原有 `app/libs/face-sdk-release.aar` 是历史二进制，尚未重新打包发布，不能据本页推定该 AAR 已具备新接口。示例 app 现在依赖 `:face-core` 源码，避免继续运行旧同步实现。

## 配置与兼容

由宿主应用在设备注册后读取受保护的服务器配置和独立设备凭据，在创建任何 SyncManager 前调用：

```java
SyncManager.configure(new SyncConfig(serverOrigin, deviceSn, deviceSecret));
SyncManager manager = new SyncManager(context);
manager.startAutoSync(30, callback);
// Activity 暂停自动触发
manager.stopAutoSync();
// 不再使用时释放线程与连接
manager.close();
```

也可使用 `new SyncManager(context, config)`。不再内置现场 IP 或演示凭据。默认要求 HTTPS；隔离开发网络确需 HTTP 时，使用五参数构造器显式设置 `allowCleartext=true`，并在宿主 Android 网络策略中单独允许。凭据应通过设备注册、安全存储和撤销流程管理，不写进代码、日志、网页或公开 AAR。

旧的 `SyncManager(Context)`、`syncData(SyncCallback)`、`startAutoSync(long, SyncCallback)`、`stopAutoSync()` 保留。未配置时明确回调错误。同步期间再次触发会合并为后续一次追赶；回调在后台线程，UI 更新须切回 UI 线程。`onSuccess(count)` 是完整在线库人数，包括 0，不能只在 count>0 时刷新识别缓存。

新源码默认采用 v2。仅在已配置受限旧设备通道时显式选择 `SyncConfig.Protocol.LEGACY_FULL`；不会因为 401、403、404、409 或网络失败自动降级为匿名全量。历史已部署 SDK 不受本地源码修改影响，必须按设备迁移清单更新应用/AAR。

## 数据与失败行为

- SQLite 从 v2 增量迁移到 v3，保留原 users；增加 sync_version、photo_url、staging、墓碑和 epoch/scopeVersion/revision 元数据。禁止直接降级旧 SDK 打开 v3 数据库；回退前备份并使用经验证的兼容版本。
- Bootstrap 接收 L/M，按 ID 取最多 200 人，完成后取得 U，再重放 (L,U]。按每人的版本比较 upsert 与墓碑，较旧事件不能覆盖较新的扫描记录。
- 增量按服务端 nextRevision 推进。即使按组授权过滤后 events 为空，也可安全推进全局游标；epoch、授权版本或 retentionFloor 不匹配则重建。中断后重新建立 staging，已确认在线库仍在，不把单页缺失人员当删除。
- 所有页、事件、最终照片成功后，在线表、墓碑与游标在一个本地事务中发布；进程内写锁覆盖发布及注册识别缓存的刷新，空库同样刷新。缓存读取失败时失效旧缓存并下次重试。要求宿主同步和识别运行在同一个应用进程；跨进程缓存需要额外的 IPC 协调。
- 照片只在重放全部事件后为最终活记录下载，复用 URL 未变且存在的本地文件；仅允许配置源站的 `/uploads/文件名`，发送设备凭据、拒绝重定向。每张照片写临时文件后发布，文件名含人员/版本和 URL 摘要。图片失败不发布半成品数据集。
- 存量中文、空格和 `+` 文件名作为单个 UTF-8 路径段编码，`+` 不转换为空格；也接受同源且已编码的 `/uploads/` URL。路径跳转、查询串、片段、凭据 URL 和双重编码被拒绝。存量字面 `%` 或其他后端禁用字符的文件名须重新上传或受控迁移，不能通过再次编码绕过校验，也不会静默改名或删字段。
- HTTP 连接时限 5 秒、读取 10 秒、总预算 15 秒；JSON 最多 16 MiB，单张照片最多 10 MiB。v1 超过 JSON 上限明确失败，不能截取前一部分当完整库。停止等待不会保证远端任务立即结束。
- WebSocket 仅提示追赶；首次连接、重连和至少 5 秒间隔的周期检查均会触发同步。所有 SyncManager 实例共用进程内同步互斥，避免多个实例同时写 staging。

## 已知容量边界

无变化的周期查询只确认游标和计数，不复制全库、不重载识别缓存。发生变化时，当前 SDK 用 SQLite 复制 staging 并事务替换在线表，磁盘工作量仍随库大小增长。原识别引擎仍把特征全量载入内存；这次保证同步正确性和网络分块，不能宣称目标 Android 设备已通过十万人识别容量验收。

重建期间需要额外 SQLite 空间；历史本地照片暂不自动批量删除，磁盘容量需监测，清理策略必须检查在线及 staging 引用。读取原生模型、ARM JNI、真实相机识别、断电和存量设备灰度仍需实机验收。

## 本地验证

使用现有 Gradle 8.2、JDK 19、Android SDK 34：

```text
gradle --no-daemon :face-core:testDebugUnitTest :app:compileDebugJavaWithJavac
```

`SyncStoreTest` 使用 Robolectric SQLite 和 loopback HTTP，覆盖迁移保留数据、中断 staging、版本与墓碑、最终空库缓存、照片失败保留在线库、先重放再下载最终照片、过滤空事件推进、URL 边界、无变化不重载、同步中 pending 信号。该验证不构建或运行 ARM native 模型，不是正式 AAR 发布。

Windows 中文路径会触发 AGP 路径检查，直接绕过还可能让测试类加载失败。本地验证使用临时且事先确认未占用的 SUBST 盘符映射本项目目录，运行后卸载；不改已有映射或项目依赖版本。
