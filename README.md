# Docker 容器化部署指南

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
