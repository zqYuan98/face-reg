# Docker 容器化部署指南

## 当前算法接入入口

新算法接入请优先阅读 [完整 API 联调文档](FaceFeatureExtractorAPI/docs/TASK-2026-001-API-DEBUGGING.md)
和 [安全边界说明](FaceFeatureExtractorAPI/docs/ALGORITHM_API_INTEGRATION.md)。公司内网 API 为
`http://<server-37>:18002`，测试台为 `http://<server-37>:18003`；历史 `18001` 未改动，不供新开发接入。

2026-09-28 服务器 37 的 `18002/18003` 仍为 API 2.1.0，包含六个 v2：portrait、witness、images、
video/quick、video/full、video/presence。线上算法提交为
`1e6b57b5b1dd32693497c21a0ce0039b4f77fc1b`；
[历史发布验收和回滚记录](FaceFeatureExtractorAPI/docs/reports/2026-09-28-presence-release-validation.md)。

当前源码/联调文档为 API 2.2.0，本轮改动尚未部署。presence 将低质量作为软提示并按预算补采，
可用特征继续比较；服务端显式试用阈值配置支持 `FOUND/true`、`NOT_FOUND_IN_SCAN/false`、
`INCONCLUSIVE/null`，返回 `CONFIGURED_UNVALIDATED`。默认无阈值仍计算证据但为
`THRESHOLD_NOT_CONFIGURED + INCONCLUSIVE/null`，请求不能自带阈值。扫描不完整或无可比脸
不能作肯定/否定结论，试用结果保持业务 REVIEW，不输出自动 PASS。
这与旧 quick/full 的持续一致性及签名 calibration 独立；不要求活体门禁，也无活体/反翻拍保证。
同名关联不等于出现标签，模型最高分不是概率或真值；独立人工标签与阈值评估见
[匿名标签说明](FaceFeatureExtractorAPI/docs/PRESENCE_LABEL_VALIDATION.md)，生产精度尚未验证。
本轮本地与匿名现场功能/覆盖结果见
[2.2.0 验证记录](FaceFeatureExtractorAPI/docs/reports/2026-09-28-presence-best-effort-validation.md)，不代表已经部署。

`GET /health/live` 只查存活；`GET /health/ready`（`/health` 别名）按能力检查模型；
`GET /openapi.json` 导入契约。legacy `/api/detect_face` 按开关注册，
`/api/extract_feature` 默认关闭/404，不为 presence 开启权限。

下文是历史全栈 Docker 部署说明，旧端口、feature_server 和算法特征接口不构成当前
模块化算法服务的接入契约；部署/回滚应按当前交付配置执行。

本指南将帮助您使用 Docker 和 Docker Compose 一键部署整个系统（后端、算法服务、数据库）。

## 1. 目录结构

确保您的项目目录结构如下（已自动为您创建了相关 Docker 文件）：

```
Face-app-web/
├── docker-compose.yml          # [新增] 编排文件
├── backend-java/
│   ├── Dockerfile              # [新增] Java 后端镜像构建文件
│   ├── src/
│   └── pom.xml
└── FaceFeatureExtractorAPI/
    ├── Dockerfile              # [新增] Python 算法镜像构建文件
    ├── feature_server.py
    └── requirements.txt
```

## 2. 部署步骤

### 第一步：安装 Docker
请确保服务器已安装 Docker 和 Docker Compose。
- **Docker**: `curl -fsSL https://get.docker.com | bash`
- **Compose**: (Docker Desktop 默认包含，Linux 可能需单独安装)

### 第二步：构建并启动
在项目根目录（`Face-app-web`）下执行：

```bash
docker-compose up -d --build
```

- `-d`: 后台运行
- `--build`: 强制重新构建镜像（首次运行或代码修改后需要）

### 第三步：验证服务
执行 `docker-compose ps` 查看容器状态，应看到三个服务 (`face-mysql`, `face-algorithm`, `face-backend`) 均为 [Up](file:///e:/Face-app-web/android-sdk/face-core/src/main/java/com/bonuos/facesdk/FaceDatabaseHelper.java#45-52) 状态。

- **Java 后端**: 访问 `http://localhost:8080/index.html`->18080
- **算法接口**: 访问 `http://localhost:8000/health`->18000
- **数据库**: 端口 3306->13306

## 3. 常见维护操作

**查看日志:**
```bash
docker-compose logs -f face-backend   # 查看 Java 后端日志
docker-compose logs -f face-algorithm # 查看 Python 算法日志
```

**停止服务:**
```bash
docker-compose down
```

**更新代码后重发布:**
```bash
# 1. 拉取/更新代码
# 2. 重启并构建
docker-compose up -d --build
```

## 4. 配置说明

在 [docker-compose.yml](file:///e:/Face-app-web/docker-compose.yml) 中，我们通过环境变量覆盖了默认配置，使其适应 Docker 网络：

- `SPRING_DATASOURCE_URL`: 指向 `mysql` 容器名而非 localhost。
- `FACE_EXTRACTOR_API_URL`: 指向 `face-algorithm` 容器名。

如果您修改了数据库密码或其他配置，请同步修改 [docker-compose.yml](file:///e:/Face-app-web/docker-compose.yml) 中的 `environment` 部分。
