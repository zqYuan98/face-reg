# 人脸识别系统 API 接口文档

本文档详细说明了人脸识别系统的后端业务接口与算法服务接口，供开发人员集成使用。

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

## 2. 算法服务接口 (Python - FaceFeatureExtractor)

该接口通常由 Java 后端内部调用，但如果开发人员需要独立调试算法或进行集成，可直接调用该微服务。

**功能**: 接收图片，进行质量检测（人脸检测、模糊度、亮度、姿态等），合格后提取 1024 维人脸特征向量。

**默认端口**: `8000` (具体取决于 docker-compose 配置)

### 2.1 提取人脸特征
*   **接口地址**: `/api/extract_feature`
*   **请求方式**: `POST`
*   **请求类型**: `multipart/form-data`
*   **请求参数**:

    | 参数名 | 类型 | 必填 | 说明 |
    | :--- | :--- | :--- | :--- |
    | image | File | 是 | 图片文件 (支持 jpg, png 等常见格式) |

*   **返回结果 (JSON)**:

    **成功响应**:
    ```json
    {
        "success": true,
        "message": "Success",
        "feature": [0.123, -0.456, ...],  // 1024维浮点数组
        "feature_dim": 1024,
        "processing_time": 0.045
    }
    ```

    **失败/质量不合格响应**:
    ```json
    {
        "success": false,
        "message": "No face detected" // 或 "Face quality check failed...", "Server error..."
    }
    ```

### 2.2 健康检查
*   **接口地址**: `/health`
*   **请求方式**: `GET`
*   **返回结果**: `{"status": "healthy", "service": "Face Feature Extractor"}`

### 2.3 人脸检测 (获取坐标)
*   **接口地址**: `/api/detect_face`
*   **请求方式**: `POST`
*   **请求类型**: `multipart/form-data`
*   **请求参数**:

    | 参数名 | 类型 | 必填 | 说明 |
    | :--- | :--- | :--- | :--- |
    | image | File | 是 | 图片文件 |
    | expand_scale | Float | 否 | 扩充比例，默认 0.0。例如 0.3 表示长宽各扩充 30% |

*   **返回结果**:

    > **坐标说明**:
    > *   `x1`, `y1`: 人脸检测框 **左上角** 的像素坐标。
    > *   `x2`, `y2`: 人脸检测框 **右下角** 的像素坐标。
    > *   `score`: 检测置信度 (0-1之间)。
    > *   **注意**: 即使设置了 `expand_scale`，返回的坐标也会被限制在图片边界内 (Clip to bounds)。

    ```json
    {
        "success": true,
        "message": "Success",
        "faces": [
            {
                "x1": 100.0,
                "y1": 50.0,
                "x2": 200.0,
                "y2": 150.0,
                "score": 0.98
            }
        ],
        "processing_time": 0.02
    }
    ```
