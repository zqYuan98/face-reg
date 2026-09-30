# Docker 容器化部署指南

## 当前算法接入入口

新算法接入请优先阅读 [完整 API 联调文档](FaceFeatureExtractorAPI/docs/TASK-2026-001-API-DEBUGGING.md)
和 [安全边界说明](FaceFeatureExtractorAPI/docs/ALGORITHM_API_INTEGRATION.md)。公司内网 API 为
`http://<server-37>:18002`，测试台为 `http://<server-37>:18003`；历史 `18001` 未改动，不供新开发接入。

2026-09-30 源码契约为 API `2.3.0`，处于构建/候选验证阶段，不代表已完成线上切换。
本次部署状态、验收证据与回滚入口统一见
[白底人脸照发布记录](FaceFeatureExtractorAPI/docs/reports/2026-09-30-attendance-face-portrait-release.md)。
六个 v2 路由不变：portrait、witness、images、video/quick、video/full、video/presence。
白底人脸照不再强制完整头顶、颈部或肩部；请求 `image` 和响应字段不变，仅新增
`PORTRAIT_SOURCE_LIMITED` / `PORTRAIT_UPSCALE_LIMITED` 原因枚举。
出图按 `success=true && image_base64` 判断；`REVIEW` 可表示已生成但有质量提醒，不能只接受 PASS。

升级前基线：2026-09-28 服务器 37 的 `18002/18003` 发布 API `2.2.0`，当时均 healthy，
发布代码为 `d78e723b8fb8737b09e12859e57aa9856a640db6`；证据见
[2.2.0 历史发布记录](FaceFeatureExtractorAPI/docs/reports/2026-09-28-presence-best-effort-release.md)。
同日后续仅 presence 运行配置改为用户指定试用阈值 `0.5`（`configured`），返回
`CONFIGURED_UNVALIDATED`，业务仍需人工 REVIEW；该次阈值调整未改代码、images/quick/full、18001 或测试台。
本次 2.3.0 升级保留该阈值与 ID，配置依据见
[0.5 试用阈值记录](FaceFeatureExtractorAPI/docs/reports/2026-09-28-presence-threshold-050.md)。

2.3.0 沿用既有 presence 契约：将低质量作为软提示并按预算补采，
可用特征继续比较；服务端显式试用阈值配置支持 `FOUND/true`、`NOT_FOUND_IN_SCAN/false`、
`INCONCLUSIVE/null`，返回 `CONFIGURED_UNVALIDATED`。源码默认未配置阈值时仍计算证据但为
`THRESHOLD_NOT_CONFIGURED + INCONCLUSIVE/null`，请求不能自带阈值。扫描不完整或无可比脸
不能作肯定/否定结论，试用结果保持业务 REVIEW，不输出自动 PASS。
这与旧 quick/full 的持续一致性及签名 calibration 独立；不要求活体门禁，也无活体/反翻拍保证。
同名关联不等于出现标签，模型最高分不是概率或真值；独立人工标签与阈值评估见
[匿名标签说明](FaceFeatureExtractorAPI/docs/PRESENCE_LABEL_VALIDATION.md)，生产精度尚未验证。
2026-09-28 历史本地与匿名现场功能/覆盖结果见
[2.2.0 本地验证记录](FaceFeatureExtractorAPI/docs/reports/2026-09-28-presence-best-effort-validation.md)；
这些历史统计不代表 2.3.0 已上线，也不替代人工标签和生产精度验收。

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
执行 `docker-compose ps` 查看容器状态，应看到三个服务 (`face-mysql`, `face-algorithm`, `face-backend`) 均为 `Up` 状态。

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

在 [docker-compose.yml](docker-compose.yml) 中，我们通过环境变量覆盖了默认配置，使其适应 Docker 网络：

- `SPRING_DATASOURCE_URL`: 指向 `mysql` 容器名而非 localhost。
- `FACE_EXTRACTOR_API_URL`: 指向 `face-algorithm` 容器名。

如果您修改了数据库密码或其他配置，请同步修改 [docker-compose.yml](docker-compose.yml) 中的 `environment` 部分。
