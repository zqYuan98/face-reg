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

2026-09-28 presence 候选版本；发布 commit 和线上证据待补，不代表已部署。
内网 Base URL：`http://<server-37>:18002`；人工测试台：`http://<server-37>:18003`。
新开发不要调用保留回退服务 `18001`。算法不访问业务库、不保存合同状态或持久化客户媒体。

### 2.1 全部接口索引

| 方法与路径 | 请求 | 响应/用途 |
|---|---|---|
| `GET /health/live` | 无 | `{"status":"live"}`；仅进程存活 |
| `GET /health/ready` | 无 | 200/503，模型与五个能力 readiness，需检查目标能力 |
| `GET /health` | 无 | ready 的兼容别名，不是仅检查存活 |
| `GET /openapi.json` | 无 | 类型化请求/响应契约；`/docs`、`/redoc` 关闭 |
| `POST /api/v2/portrait/generate` | multipart `image` | 白底头肩照，30 秒 |
| `POST /api/v2/witness/blur-background` | multipart `image` | 人物优先背景虚化，30 秒 |
| `POST /api/v2/verify/images` | multipart `reference_image,probe_image` | 两图比对，30 秒 |
| `POST /api/v2/verify/video/quick` | multipart `reference_image,video` | 持续身份一致性快检，60 秒 |
| `POST /api/v2/verify/video/full` | multipart `reference_image,video` | 持续身份一致性完整检查，600 秒 |
| `POST /api/v2/verify/video/presence` | multipart `reference_image,video` | 目标出现候选扫描，600 秒，不是持续一致性 |
| `POST /api/detect_face` | multipart `image,expand_scale?` | 按 legacy 检测开关注册，候选默认开，无特征返回 |
| `POST /api/extract_feature` | multipart `image`，受控内部鉴权 | 候选默认关闭/404，不为新增功能开启 |

### 2.2 结果、资源与安全边界

先检查 HTTP；200 不等于身份通过。旧图片/quick/full 看标定后的 suggestion；presence 的
身份结论只读 `decision/found`，不把 `success` 或分数当同一人结论。首版 presence 必定是
`calibration_status=UNCALIBRATED,decision=INCONCLUSIVE,found=null`，没有可用脸也不能转成 false。
FOUND/NOT_FOUND_IN_SCAN 为保留状态，本版不产出；所有视频均无活体/反翻拍保证。

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
