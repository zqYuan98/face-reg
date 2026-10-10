# 人脸识别平台

目录包含 Java 管理后台、Python 算法服务和 Android SDK。当前后台提供服务端分页、独立管理/设备鉴权，以及 v1 完整快照和 v2 分页增量同步。

算法服务通过 `feature_comparison_server.py` 启动时提供检测、特征提取和特征比对三个计算接口。新增 `POST /api/compare_feature` 接收 JSON 中的两个 1024 维特征，接口与旧服务升级方式见 [特征比对接口](FaceFeatureExtractorAPI/FEATURE_COMPARISON.md)。

修复结果和实际测试见 [验证记录](docs/reports/2026-09-24-backend-remediation-results.md)。本地测试通过不代表生产已部署或真机容量已验收。

## 启动前

先阅读 [迁移与回滚手册](docs/reports/2026-09-24-backend-remediation-runbook.md)。已有数据库必须备份并按手册显式执行迁移；应用启动不会自动补表。新库才使用 schema.sql。已有 MySQL volume 不会自动应用初始化脚本或重新创建应用账号。

Compose 必须从受保护的运行环境获得以下值，仓库没有默认密码：

- `MYSQL_ROOT_PASSWORD`：数据库管理员密码。
- `FACE_DB_PASSWORD`：`face_app` 应用账号密码。
- `FACE_ADMIN_PASSWORD_HASH`：管理密码的 BCrypt 哈希。
- `FACE_DEVICE_CREDENTIALS_JSON`：按需配置设备凭据，默认 `[]` 拒绝设备访问。

生产 Compose 固定开启 Secure 会话 Cookie，需要同源 HTTPS 反向代理。直接 HTTP 打开页面不能作为生产登录方式；只在隔离本机验证时按安全说明单独关闭 Secure。

## 容器部署

在本目录、完成配置与迁移后执行：

```sh
docker compose up -d --build
docker compose ps
```

主机发布地址仅绑定 loopback：后台 `127.0.0.1:18080`，内部算法 `127.0.0.1:18000`，MySQL `127.0.0.1:13307`。通过已配置的 HTTPS 入口访问管理页面；不要把数据库或算法端口直接暴露给设备网络。

```sh
docker compose logs -f face-backend
docker compose logs -f face-algorithm
docker compose stop
```

`restart: always` 只处理进程退出，不会自动重启仍存活但原生推理挂起的进程；就绪降级后的监督重启须按 [算法说明](FaceFeatureExtractorAPI/BOUNDED_EXECUTION.md) 接入现场监控。不要为修复配置而删除数据库 volume。

## 接口、SDK 和验证

- [API 契约](API_DOCUMENTATION.md)：管理分页、批量结果、真实 HTTP 状态和认证流程。
- [安全迁移](backend-java/SECURITY_MIGRATION.md)：管理身份、设备范围、默认关闭的旧设备迁移窗口。
- [SDK v2 接入](android-sdk/SYNC_V2.md)：新版源代码与示例 app，旧 AAR 未自动替换。
- [本地测试及只读巡检](backend-java/tools/README.md)：Java/MySQL、前端、容量和照片清单工具。

当前没有导出接口。新增导出时应另行设计异步任务、流式生成、配额和下载有效期，不能复用不受限制的列表查询。
