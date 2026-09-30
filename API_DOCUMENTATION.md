# 人脸识别系统 API 接口文档

本文档详细说明了人脸识别系统的后端业务接口与算法服务接口，供开发人员集成使用。

算法部分已迁移到模块化 v2，完整请求/响应、鉴权、资源、错误与调用示例以
[API 联调文档](FaceFeatureExtractorAPI/docs/TASK-2026-001-API-DEBUGGING.md) 为准。
下面第 1 节为历史 Java 业务接口描述，其中旧检索阈值不适用于 v2 或 presence。

## 1. 系统业务接口 (Java Backend)

后端服务主要提供分组管理和人员信息管理功能。所有接口统一通过 HTTP 协议调用，返回 JSON 格式数据。

**基础路径**: `/api` (例如: `http://<server_ip>:<port>/api`)

### 1.1 分组管理

#### 1.1.1 创建/添加分组
*   **接口地址**: `/groups/add`
*   **请求方式**: `POST`
*   **请求类型**: `application/json`
*   **请求参数**:
    
    | 参数名 | 类型 | 必填 | 说明 |
    | :--- | :--- | :--- | :--- |
    | name | String | 是 | 分组名称 |
    | description | String | 否 | 分组描述 (如果有) |

    **请求示例**:
    ```json
    {
        "name": "VIP人员",
        "description": "重要客户分组"
    }
    ```

*   **返回结果**:
    ```json
    {
        "code": 200,
        "msg": "success",
        "data": {
            "id": 1,
            "name": "VIP人员",
            "createTime": "2023-12-30T10:00:00"
        }
    }
    ```

#### 1.1.2 获取分组列表
*   **接口地址**: `/groups/list`
*   **请求方式**: `GET`
*   **返回结果**:
    ```json
    {
        "code": 200,
        "msg": "success",
        "data": [
            {
                "id": 1,
                "name": "默认分组"
            },
            {
                "id": 2,
                "name": "员工分组"
            }
        ]
    }
    ```

---

### 1.2 人员管理

#### 1.2.1 新增人员 (带照片)
该接口用于注册新用户，同时上传人脸照片用于提取特征。

*   **接口地址**: `/users/add`
*   **请求方式**: `POST`
*   **请求类型**: `multipart/form-data` (表单上传)
*   **请求参数**:

    | 参数名 | 类型 | 必填 | 说明 |
    | :--- | :--- | :--- | :--- |
    | name | String | 是 | 人员姓名 |
    | groupId | Long | 否 | 所属分组ID |
    | photo | File | 是 | 人脸照片文件 (jpg/png) |

*   **返回结果**:
    ```json
    {
        "code": 200,
        "msg": "success",
        "data": {
            "id": 101,
            "name": "张三",
            "groupId": 2,
            "feature": "..." // (内部可能不直接返回长特征，视具体实现而定)
        }
    }
    ```

#### 1.2.2 编辑人员信息
用于更新人员姓名、分组或重新上传人脸照片。

*   **接口地址**: `/users/update/{id}`
*   **请求方式**: `POST`
*   **请求类型**: `multipart/form-data`
*   **路径参数**:
    *   `id`: 人员ID (Long)

*   **请求参数**:

    | 参数名 | 类型 | 必填 | 说明 |
    | :--- | :--- | :--- | :--- |
    | name | String | 是 | 人员姓名 |
    | groupId | Long | 否 | 所属分组ID (不传则不修改) |
    | photo | File | 否 | 新的人脸照片 (不传则不修改) |

*   **返回结果**:
    ```json
    {
        "code": 200,
        "msg": "success",
        "data": { ... }
    }
    ```

#### 1.2.3 获取人员列表 (查询)
*   **接口地址**: `/users/list`
*   **请求方式**: `GET`
*   **请求参数**:

    | 参数名 | 类型 | 必填 | 说明 |
    | :--- | :--- | :--- | :--- |
    | name | String | 否 | 按姓名模糊查询 |
    | groupId | Long | 否 | 按分组ID筛选 |

*   **返回结果**:
    ```json
    {
        "code": 200,
        "msg": "success",
        "data": [ ...User Objects... ]
    }
    ```
    
#### 1.2.4 人脸搜索 (1:N 检索)
用于上传一张人脸照片，并在指定分组内检索最相似的人员。

*   **接口地址**: `/users/search`
*   **请求方式**: `POST`
*   **请求类型**: `multipart/form-data`
*   **请求参数**:

    | 参数名 | 类型 | 必填 | 说明 |
    | :--- | :--- | :--- | :--- |
    | photo | File | 是 | 待检索的人脸照片 |
    | groupId | Long | 是 | 目标分组ID |

*   **返回结果**:
    
    **成功找到匹配**:
    ```json
    {
        "code": 200,
        "msg": "success",
        "data": {
            "id": 101,
            "name": "张三",
            "groupId": 2,
            ...
        }
    }
    ```

    **未找到匹配 (相似度低于阈值 0.6)**:
    ```json
    {
        "code": 404,
        "msg": "未找到匹配用户"
    }
    ```

---

## 2. 当前算法服务（模块化 v2）

本节对应 2026-09-30 已正式部署的 API `2.3.1`（代码 `4e6a337`）；
服务器 37 的 `18002/18003` 均 healthy，8/8 模型就绪，portrait 使用 `face_archive`。
本次部署状态、各项验收完成状态及回滚入口以 [检测框越界修正发布记录](FaceFeatureExtractorAPI/docs/reports/2026-09-30-portrait-box-overflow-release.md) 为准。
直接升级基线为 `2.3.0 / 2557e17`，原 API/测试台容器已停止保留；回滚恢复该版本与原 presence `0.5` 配置，
不是更早的 `d78 / 2.2.0`。本次配置字节哈希不变。
更早历史：2026-09-28 服务器 37 的 `18002/18003` 发布 `2.2.0`，当时均 healthy，
发布代码为 `d78e723b8fb8737b09e12859e57aa9856a640db6`；
该次证据见 [2.2.0 历史发布记录](FaceFeatureExtractorAPI/docs/reports/2026-09-28-presence-best-effort-release.md)。
37 在同日后续按用户指定将 presence 运行配置设为试用阈值 `0.5`（`configured`），返回
`CONFIGURED_UNVALIDATED`，业务仍需人工 REVIEW；不表示生产精度已验证。
该次阈值调整只改 presence 运行配置，未改源码默认无阈值配置、images/quick/full、18001 或测试台；
本次 2.3.1 升级继续保留视频 `0.5` 与原阈值 ID，
详见 [0.5 试用阈值记录](FaceFeatureExtractorAPI/docs/reports/2026-09-28-presence-threshold-050.md)。
内网 Base URL：`http://<server-37>:18002`；人工测试台：`http://<server-37>:18003`。
新开发不要调用历史服务 `18001`，本次未改动该服务。算法不访问业务库、不保存合同状态或持久化客户媒体。

### 2.1 全部接口索引

| 方法与路径 | 请求 | 响应/用途 |
|---|---|---|
| `GET /health/live` | 无 | `{"status":"live"}`；仅进程存活 |
| `GET /health/ready` | 无 | 200/503，模型与五个能力 readiness，需检查目标能力 |
| `GET /health` | 无 | ready 的兼容别名，不是仅检查存活 |
| `GET /openapi.json` | 无 | 类型化请求/响应契约，当前 `info.version=2.3.1`；`/docs`、`/redoc` 关闭 |
| `POST /api/v2/portrait/generate` | multipart `image` | 白底人脸照，30 秒；成功图片可带 REVIEW 质量提醒 |
| `POST /api/v2/witness/blur-background` | multipart `image` | 人物优先背景虚化，30 秒 |
| `POST /api/v2/verify/images` | multipart `reference_image,probe_image` | 两图比对，30 秒 |
| `POST /api/v2/verify/video/quick` | multipart `reference_image,video` | 持续身份一致性快检，60 秒 |
| `POST /api/v2/verify/video/full` | multipart `reference_image,video` | 持续身份一致性完整检查，600 秒 |
| `POST /api/v2/verify/video/presence` | multipart `reference_image,video` | 目标出现候选扫描，600 秒，不是持续一致性 |
| `POST /api/detect_face` | multipart `image,expand_scale?` | 按 legacy 检测开关注册，候选默认开，无特征返回 |
| `POST /api/extract_feature` | multipart `image`，受控内部鉴权 | 候选默认关闭/404，不为新增功能开启 |

### 2.2 结果、资源与安全边界

先检查 HTTP；200 不等于身份通过。旧图片/quick/full 看标定后的 suggestion；presence 的
匹配结论只读 `decision/found`，不把 `success` 或分数当同一人结论。沿用 2.2.0 presence 契约：服务端显式
`configured` 阈值与 ID 返回 `CONFIGURED_UNVALIDATED`，可产生 `FOUND/true`、
`NOT_FOUND_IN_SCAN/false`、`INCONCLUSIVE/null`；使用源码默认无阈值配置时仍计算证据，附
`THRESHOLD_NOT_CONFIGURED`，判定为 INCONCLUSIVE/null。没有默认身份阈值，请求不接受调用方阈值。
扫描不完整、参考无效、无可比脸或全局失败都不给肯定/否定结果；低于阈值但有候选截断/局部失败
也为 null。试用 `suggestion=REVIEW` 不能遮盖 FOUND，更不能转成业务自动 PASS。

2.3.0 portrait 的请求 `image` 和全部响应字段保持不变，已引入 `PORTRAIT_SOURCE_LIMITED` /
`PORTRAIT_UPSCALE_LIMITED` 原因枚举。以 `success=true && image_base64` 判断出图；成功 `REVIEW`
仍显示/下载图片并展示 `reason_codes`，不表示身份或考勤通过。仓库显式配置 `face_archive`，
不强制完整头顶/颈部/肩部；旧 YAML 缺 mode 保持 `head_shoulders`，上线须显式迁移。

2.3.1 补丁仅对 `face_archive` 容忍轻微检测框外扩：各侧溢出分别按原框宽/高归一化后
均不超过 5%，裁至图内的可见面积至少为原框的 90%；目标选择、五点几何有效且五点全在源图内，
裁后宽高均至少 112px，并通过后续抠图检查时，返回有图的 `REVIEW + PORTRAIT_SOURCE_LIMITED`。
严重越界、无效/越界关键点、裁后脸过小、推理异常或坏蒙版仍失败，不返回派生图。
检测框越界不能单独证明真实脸部缺失；仅使用源图真实像素，不补造像素、不提取 embedding。
原 18 个响应字段、原因枚举、480×640 JPEG 和 30 秒预算不变；不改变严格 `head_shoulders`、
witness、images、quick、full 或 presence。联调时须核对 OpenAPI `info.version=2.3.1`。

presence 的暗/糊/侧脸/小脸/贴边为软提示；能形成有效 1024 维特征就比对，视频 64px 不是硬下限。
默认 0.5 秒基础采样、每窗口最多 2 帧按需补采，总 1200 分析帧预算包含首尾和补采；长视频
必要时增大实际基础间隔。响应区分比较状态、请求/实际间隔、基础/补采/成功与失败候选计数、
阈值/ID/指纹及每帧 candidate_index/quality_warnings。输入资源超限仍为 400/413/504。
presence 不要求活体或签名校准门禁，不改变旧五接口；所有视频均无活体/反翻拍保证。
最高分不是概率或出现真值，NOT_FOUND_IN_SCAN 不是整片绝对不存在。人工完整视频标签及
人员隔离的 tuning/evaluation 见 [标签说明](FaceFeatureExtractorAPI/docs/PRESENCE_LABEL_VALIDATION.md)；无标签只报告覆盖，生产精度未验证。

图片仅 JPEG/PNG，每张≤15 MiB、25 MP（witness≤16 MP）；视频≤500 MiB、600 秒、120 FPS。
MP4/AVI/MOV 等经现有上传路径处理，但实际编码须在目标镜像验证。整个 multipart body
也有限制；full/presence 应由业务异步 worker 调用。v2 不返回特征数组、原图、帧或私有路径。

v2 内网接口无应用层 Authorization 要求，默认无 CORS；公网入口必须另配 TLS、认证、
授权、限流和审计。测试台有 Origin/CSRF/白名单保护，不是业务 API、也不是公网安全网关。
400/413 修正上传，503 检查模型，504 按幂等补偿，500 联系运维；错误体和所有 DTO 的完整
字段见 [API 联调文档](FaceFeatureExtractorAPI/docs/TASK-2026-001-API-DEBUGGING.md)。

### 2.3 兼容接口

`detect_face` 的 `expand_scale` 默认为 0、范围 `[0,2]` 且必须有限。响应为
`success:boolean,message:string,faces:[{x1,y1,x2,y2,score:number}],processing_time:number`，
坐标裁切至图片边界，耗时单位秒；无脸时 faces 为空、success=false，HTTP 200。

`extract_feature` 默认不注册。仅在批准的迁移环境中同时满足开关、高熵内部令牌、客户端
CIDR、可信代理链和 `X-Internal-Token` 后才可用，否则 403；禁用时 404。不要为联调打开。
其唯一特征例外、完整请求/响应和责任边界见联调文档第 15 节及
[安全接入说明](FaceFeatureExtractorAPI/docs/ALGORITHM_API_INTEGRATION.md)。
