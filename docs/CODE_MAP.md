# 人脸识别平台代码地图

说明仓库里各端代码在哪里、各分支的状态、37 服务器上每个服务跑的是哪个提交。代码或部署变化时更新本文，不另写平行文档。

## 1. 各端代码

| 端 | 目录 | 语言 / 构建 | 作用 | 主要文档 |
|---|---|---|---|---|
| 算法服务 | `FaceFeatureExtractorAPI/` | Python，FastAPI + ONNX Runtime；镜像 `Dockerfile`（CPU）、`Dockerfile.gpu`（GPU）、`Dockerfile.compare`（叠加在 1.1.0 旧镜像上） | 图片 → 人脸框和特征；特征 1:1 比对、调用方自带候选的 1:N 检索 | [face-http-api.md](face-http-api.md)、[FEATURE_V2.md](../FaceFeatureExtractorAPI/FEATURE_V2.md)、[FEATURE_COMPARISON.md](../FaceFeatureExtractorAPI/FEATURE_COMPARISON.md)、[BOUNDED_EXECUTION.md](../FaceFeatureExtractorAPI/BOUNDED_EXECUTION.md) |
| Java 后端 | `backend-java/` | Spring Boot 3、MyBatis-Plus、MySQL；Maven | 人员和分组管理、照片注册、1:N 检索（内存特征库）、多版本特征与补提、设备同步 v1/v2、管理端页面（`static/`） | [API_DOCUMENTATION.md](../API_DOCUMENTATION.md)、[face-http-api.md](face-http-api.md) |
| Android SDK | `android-sdk/face-core/` | Java + C++（NDK，ONNX Runtime）；Gradle | 离线引擎 `libfaceengine.so`：检测跟踪、活体、v1 / InsightFace 特征、同步存储 | [SDK_INTEGRATION.md](../android-sdk/SDK_INTEGRATION.md)、[SDK_INTERFACE.md](../android-sdk/SDK_INTERFACE.md)、[SYNC_V2.md](../android-sdk/SYNC_V2.md) |
| Android 示例 App | `android-sdk/app/` | Gradle | 刷脸识别演示，使用 face-core | 同上 |
| Android 对标台架 | `android-sdk/face-bench/` | Gradle | 与虹软同机同帧对比，录制和回放 | [release v0.1-internal](release/v0.1-internal/README.md) |
| 评测工具 | `tools/engine-eval/`、`tools/face-eval/` | Python | 设备端与服务端一致性、LFW 评测、量化、对标报告 | 各目录 README |
| 设计与记录 | `docs/`、`face-notes/` | Markdown / HTML | 设计、计划、部署记录、发布说明、对标分析 | — |

**三端共享的特征版本**

| 版本 | 维度 | 服务端 | Android | 同步 |
|---|---|---|---|---|
| `recognizer_v1_1024` | 1024 | `/api/extract_feature`；存于 `sys_user.feature_data` | `NATIVE` / `NATIVE_INT8` 引擎直接使用 | 下发 |
| `arcface_r50_512` | 512 | `/api/v2/extract_feature`；存于 `sys_user_feature` | `INSIGHTFACE_*` 引擎在设备上用注册照自行提取 | **不下发**（设备自己算，两边的特征空间相同） |

InsightFace 权重只允许非商业研究用途，不入库：服务端放在 `FaceFeatureExtractorAPI/checkpoints/insightface/`，Android 端由 `local.properties` 的 `insightface.assets` 指定。

## 2. 分支

| 分支 | 末端提交 | 状态 | 处理建议 |
|---|---|---|---|
| `yuanzq/feat/offline-face-engine` | 最新 | **集成分支**：包含全部工作（Android 离线引擎、服务端优化、v2、找回的 search）；37 的 `944765d` 上线验证通过后已合并进 `main` | 继续作为开发分支；验证后再合并 `main` |
| `yuanzq/feat/feature-search` | `4dc7d57` | 找回的 search 线（原 37 线上版本 `49eb6e7`），已合并 | 可以删除 |
| `main` | 与集成分支一致 | 37 升级验证通过后，由 `6261b20` 快进合并到集成分支 | 稳定分支：只合并已在服务器上验证过的提交 |
| `origin/feat/TASK-2026-001-modular-face-algorithm-api` | `2966b34` | **另一套独立服务**（模块化算法 API 2.4.0，含可视化控制台），跑在 37 的 18002/18003 端口；与当前分支分叉，各有几十个提交互不包含 | 不要合并进本服务；它是不同的产品线。路径也用 `/api/v2/...`，与本服务的 `/api/v2/extract_feature` 只是名字相近，是两个服务 |
| `origin/codex/backend-remediation-publish` | `cfad46f` | 已包含在当前分支里（37 上一版后端 `cfad46f`） | 可以删除 |
| `origin/codex/deploy-feature-compare` | `6261b20` | 已包含在当前分支里（旧 main） | 可以删除 |
| `origin/refactor/TASK-2026-000-platform-consolidation` | `d689452` | 已包含在当前分支里 | 可以删除 |

部署过的提交都打了标签（`git tag -l "deployed/*"`）。公开镜像 github.com/zqYuan98/face-reg 由 `D:/face-deps/publish-github.sh` 生成，已去掉模型、未授权二进制、部署记录和内网地址，`deployed/*` 标签不推送；不要从本地仓库直接推 GitHub。

## 3. 37 服务器（<server-37>）现状

升级记录：[server37-platform-944765d-deployment.md](reports/server37-platform-944765d-deployment.md)。

| 端口 | 容器 | 镜像 | 提交（标签） | 版本 / 接口 |
|---|---|---|---|---|
| 18001 | `face-algorithm` | `face-algorithm:platform-944765d` | `944765d`（`deployed/server37/face-algorithm-18001`） | 1.4.0，8 个接口；v1 + v2 均已加载；2 个进程；限制 2 CPU / 4 GB |
| 18080 | `face-backend` | `face-backend:platform-944765d` | `944765d`（`deployed/server37/face-backend-18080`） | 注册时同时提取 v1 和 v2；**检索仍为 v1**（58 人中 2 人缺 v2 特征）；内存特征库；补提接口 |
| 13307 | `face-mysql` | `mysql:8.0` | — | `face_db` 已执行 `V20261016_01`（新增 `sys_user_feature` 表） |
| 18002 / 18003 | `face-algorithm-task-2026-001` / `-visual-console-…` | `face-algorithm:task-2026-001-a49670dc9123` | `a49670dc`（`deployed/server37/face-algorithm-task-2026-001-18002`） | 另一套模块化服务 2.4.0：`/api/v2/verify/*`、`/api/v2/portrait/generate` 等 |
| 18000 | `smart-platform-auto-label` | 其他项目 | — | **注意**：仓库 `docker-compose.yml` 让算法服务占 18000，而 37 上这个端口被别的项目占用；37 用的是自己的 `/home/<user>/docker/docker-compose.yml`，算法服务在 18001。不要拿仓库里的 compose 覆盖 37 的配置 |

上一版线上提交保留了回滚标签 `deployed/server37/rollback/*`：算法服务 `49eb6e7`，后端 `cfad46f`。

**37 的特殊配置**：
- 不再挂载宿主机的 `FaceFeatureExtractorAPI/models`。旧做法会用宿主机上的旧代码覆盖镜像里的代码，新代码等于没生效。
- v2 权重放在宿主机 `FaceFeatureExtractorAPI/insightface/`，挂载到容器的 `/app/insightface`。
- 镜像在服务器上基于原镜像叠加新代码构建，配方见 `tools/deploy/server37/`。

## 4. 本次整理做了什么

1. **找回丢失的提交**：在 37 上发现 `/api/search_feature`，对应的 5 个提交（`7d59497`～`4dc7d57`）已经部署，却不在任何分支上，git 清理时可能被删除。现已挂到 `yuanzq/feat/feature-search` 分支并合并，search 同时扩展为支持 v2。
2. **按端提交另一个会话留下的未提交改动**：Android 分析线程优化（`af855e0`）、评测工具 v3（`c97e4f0`）、release 文档 05/06（`4db6b95`）、虹软 Linux SDK 分析报告（`08e7abd`）。提交前已验证：face-core 115 项、face-bench 18 项、app 3 项单测通过，原生库和三个 APK 编译通过，评测工具 125 项测试通过。
3. **虹软材料不入库**：从虹软 SDK 包中提取的头文件、示例代码、反汇编和原始工具输出只留在本地，已加入 `.gitignore`。
4. **标出线上提交**：给 37 上运行的三个提交打了标签。

## 5. 以后避免再乱的规则

1. 只从**已推送到远端的命名分支**构建部署镜像，镜像标签写提交号，并在仓库打 `deployed/<服务器>/<服务>` 标签。
2. 部署记录写在 `docs/reports/`，并与代码在同一个分支上提交。
3. 一个功能做完先合并回集成分支，再开下一个功能，不要从旧提交分叉后丢下不管。
4. 不同服务（本服务 vs TASK-2026-001 模块化服务）各自维护分支和版本号，接口路径不要混用。
