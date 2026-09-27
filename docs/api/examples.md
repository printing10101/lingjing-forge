# API 请求/响应示例

> 本文档为各 API 端点提供 **2 个完整示例**（正常场景 + 边界场景），包含请求头、请求体、响应状态码与响应体完整内容。所有示例均与当前代码实现 v2.5.0 完全匹配。

## 目录

- [1. 认证 API](#1-认证-api)
- [2. 用户管理 API](#2-用户管理-api)
- [3. LNN 推理 API](#3-lnn-推理-api)
- [4. 磨损预测 API](#4-磨损预测-api)
- [5. 异步任务 API](#5-异步任务-api)
- [6. STEP 导入 API](#6-step-导入-api)
- [7. 仿真 API](#7-仿真-api)
- [8. 项目管理 API](#8-项目管理-api)
- [9. 模板市场 API](#9-模板市场-api)
- [10. 健康检查 API](#10-健康检查-api)
- [11. 用户主权 API](#11-用户主权-api)
- [12. RAG 检索 API](#12-rag-检索-api)

> 完整错误响应格式参见 [错误码说明](./error-codes.md)。

---

## 1. 认证 API

### 1.1 用户登录

**端点**：`POST /api/v1/auth/login`

#### 正常场景（200）

**请求**：

```http
POST /api/v1/auth/login HTTP/1.1
Host: localhost:8765
Content-Type: application/json
X-Request-ID: 7f3a1b2e-9c4d-4e5f-8a6b-1c2d3e4f5a6b

{
  "username": "admin",
  "password": "YourStrongPass!2024"
}
```

**响应**：

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: 7f3a1b2e-9c4d-4e5f-8a6b-1c2d3e4f5a6b
Set-Cookie: refresh_token=...; HttpOnly; Secure; SameSite=Strict

{
  "code": 0,
  "message": "登录成功",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 1800,
    "user": {
      "id": 1,
      "username": "admin",
      "role": "sysadmin",
      "email": "admin@lingjing-mfg.com"
    }
  }
}
```

#### 边界场景：密码错误（401）

**请求**：

```http
POST /api/v1/auth/login HTTP/1.1
Host: localhost:8765
Content-Type: application/json

{
  "username": "admin",
  "password": "WrongPassword"
}
```

**响应**：

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
X-Request-ID: ...

{
  "code": 1003,
  "error_code": "E1003",
  "message": "认证失败",
  "detail": "用户名或密码错误",
  "suggestion": "请确认用户名和密码后重试。连续 5 次错误后账号将被锁定 30 分钟。",
  "recoverable": true,
  "request_id": "..."
}
```

#### 边界场景：账号被锁定（423）

**请求**：（连续 5 次错误后）

**响应**：

```http
HTTP/1.1 423 Locked
Content-Type: application/json

{
  "code": 1003,
  "error_code": "E1003",
  "message": "账号已锁定",
  "detail": "由于连续 5 次登录失败，账号已被临时锁定。",
  "suggestion": "请等待 30 分钟后重试，或联系系统管理员重置密码。",
  "recoverable": false,
  "request_id": "..."
}
```

---

### 1.2 用户注册

**端点**：`POST /api/v1/auth/register`

#### 正常场景（201）

**请求**：

```http
POST /api/v1/auth/register HTTP/1.1
Host: localhost:8765
Content-Type: application/json

{
  "username": "engineer01",
  "password": "EngineerPass!2024",
  "email": "engineer01@lingjing-mfg.com",
  "display_name": "张工",
  "role": "engineer"
}
```

**响应**：

```http
HTTP/1.1 201 Created
Content-Type: application/json

{
  "code": 0,
  "message": "用户创建成功",
  "data": {
    "id": 42,
    "username": "engineer01",
    "email": "engineer01@lingjing-mfg.com",
    "role": "engineer",
    "created_at": "2024-05-12T10:30:00Z",
    "is_active": true
  }
}
```

#### 边界场景：弱密码（400）

**请求**：

```json
{
  "username": "engineer02",
  "password": "123456",
  "email": "engineer02@lingjing-mfg.com"
}
```

**响应**：

```http
HTTP/1.1 400 Bad Request

{
  "code": 1002,
  "error_code": "E1002",
  "message": "请求参数无效",
  "detail": "密码强度不足：长度必须 ≥12 位，必须包含大小写字母、数字、特殊字符",
  "suggestion": "请使用更强的密码，例如 'Engineer2024!Pass'",
  "recoverable": false,
  "request_id": "..."
}
```

---

## 2. 用户管理 API

### 2.1 获取当前用户信息

**端点**：`GET /api/v1/users/me`

#### 正常场景（200）

**请求**：

```http
GET /api/v1/users/me HTTP/1.1
Host: localhost:8765
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**响应**：

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "code": 0,
  "message": "操作成功",
  "data": {
    "id": 1,
    "username": "admin",
    "email": "admin@lingjing-mfg.com",
    "display_name": "系统管理员",
    "role": "sysadmin",
    "is_active": true,
    "created_at": "2023-06-01T00:00:00Z",
    "last_login": "2024-05-12T08:30:00Z",
    "mfa_enabled": true,
    "permissions": [
      "system:read", "system:write",
      "user:read", "user:write", "user:delete",
      "project:read", "project:write", "project:delete",
      "lnn:read", "lnn:write", "lnn:train"
    ]
  }
}
```

#### 边界场景：Token 过期（401）

**请求**：携带已过期的 JWT

**响应**：

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer error="invalid_token", error_description="Token expired"

{
  "code": 1003,
  "error_code": "E1003",
  "message": "Token 已过期",
  "detail": "JWT token 已于 2024-05-12T10:00:00Z 过期",
  "suggestion": "请使用 refresh_token 刷新访问令牌，或重新登录。",
  "recoverable": true,
  "request_id": "..."
}
```

---

## 3. LNN 推理 API

### 3.1 模型推理

**端点**：`POST /api/v1/lnn/predict`

#### 正常场景（200）

**请求**：

```http
POST /api/v1/lnn/predict HTTP/1.1
Host: localhost:8765
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
Content-Type: application/json
X-Request-ID: predict-2024-05-12-001

{
  "model_name": "cutting_force",
  "input_data": [[1.2, 0.8, 0.5, 1.0, 0.3]],
  "return_confidence": true
}
```

**响应**：

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: predict-2024-05-12-001

{
  "code": 0,
  "message": "推理成功",
  "data": {
    "value": 845.32,
    "confidence": 0.92,
    "inference_time": 23.5,
    "model_info": {
      "name": "cutting_force",
      "version": "v1.2.0",
      "last_updated": "2024-04-15T00:00:00Z"
    }
  }
}
```

#### 边界场景：批量推理

**请求**：

```json
{
  "model_name": "wear_prediction",
  "input_data": [
    [1.2, 0.8, 0.5, 1.0, 0.3, 150.0],
    [1.5, 1.0, 0.6, 1.2, 0.4, 180.0],
    [2.0, 1.2, 0.8, 1.5, 0.5, 200.0]
  ],
  "return_confidence": true
}
```

**响应**：

```json
{
  "code": 0,
  "message": "推理成功",
  "data": {
    "value": [0.18, 0.32, 0.45],
    "confidence": [0.88, 0.85, 0.79],
    "inference_time": 41.2,
    "model_info": {
      "name": "wear_prediction",
      "version": "v1.1.5",
      "last_updated": "2024-03-20T00:00:00Z"
    }
  }
}
```

#### 边界场景：模型不存在（404）

**请求**：

```json
{
  "model_name": "non_existent_model",
  "input_data": [[1.0, 0.5, 0.3, 0.8, 0.2]],
  "return_confidence": false
}
```

**响应**：

```http
HTTP/1.1 404 Not Found

{
  "code": 4001,
  "error_code": "E4001",
  "message": "LNN 模型未找到",
  "detail": "模型 'non_existent_model' 未在注册表中",
  "suggestion": "请使用 GET /api/v1/lnn/models 查看可用模型列表。可用模型：cutting_force, wear_prediction, tool_life, surface_roughness。",
  "recoverable": false,
  "request_id": "..."
}
```

#### 边界场景：输入数据维度错误（400）

**请求**：

```json
{
  "model_name": "cutting_force",
  "input_data": [[1.2, 0.8]],  // 模型期望 5 维
  "return_confidence": false
}
```

**响应**：

```http
HTTP/1.1 400 Bad Request

{
  "code": 1002,
  "error_code": "E1002",
  "message": "请求参数无效",
  "detail": "输入特征维度不匹配：模型 'cutting_force' 期望 5 维，实际传入 2 维",
  "suggestion": "请按照模型规格填充特征：[主轴转速, 进给速度, 切深, 切宽, 切削速度]",
  "recoverable": false,
  "request_id": "..."
}
```

---

### 3.2 模型训练

**端点**：`POST /api/v1/lnn/train`

#### 正常场景：启动训练（200）

**请求**：

```http
POST /api/v1/lnn/train HTTP/1.1
Host: localhost:8765
Authorization: Bearer ...

{
  "model_name": "cutting_force",
  "data_path": "/data/bosch_cutting_force.csv",
  "hyperparameters": {
    "learning_rate": 0.001,
    "epochs": 100,
    "batch_size": 32,
    "optimizer": "adam"
  }
}
```

**响应**：

```http
HTTP/1.1 200 OK

{
  "code": 0,
  "message": "训练任务已启动",
  "data": {
    "task_id": "train-cf-20240512-103045-a1b2c3",
    "status": "queued",
    "estimated_time_seconds": 1800
  }
}
```

#### 边界场景：训练数据文件不存在（404）

**请求**：

```json
{
  "model_name": "cutting_force",
  "data_path": "/data/missing.csv",
  "hyperparameters": { "learning_rate": 0.001, "epochs": 100, "batch_size": 32, "optimizer": "adam" }
}
```

**响应**：

```http
HTTP/1.1 404 Not Found

{
  "code": 5005,
  "error_code": "E5005",
  "message": "数据文件未找到",
  "detail": "训练数据文件 '/data/missing.csv' 不存在或无读权限",
  "suggestion": "请确认文件路径正确，且运行用户有读权限。",
  "recoverable": false,
  "request_id": "..."
}
```

---

### 3.3 列出所有模型

**端点**：`GET /api/v1/lnn/models`

#### 正常场景（200）

**请求**：

```http
GET /api/v1/lnn/models HTTP/1.1
Host: localhost:8765
Authorization: Bearer ...
```

**响应**：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": {
    "models": [
      {
        "name": "cutting_force",
        "type": "cfc",
        "version": "v1.2.0",
        "status": "loaded",
        "input_dim": 5,
        "output_dim": 1,
        "size_mb": 12.4,
        "last_updated": "2024-04-15T00:00:00Z"
      },
      {
        "name": "wear_prediction",
        "type": "ltc",
        "version": "v1.1.5",
        "status": "loaded",
        "input_dim": 6,
        "output_dim": 1,
        "size_mb": 18.7,
        "last_updated": "2024-03-20T00:00:00Z"
      }
    ],
    "total": 2
  }
}
```

---

## 4. 磨损预测 API

### 4.1 提交磨损预测任务

**端点**：`POST /api/v1/wear-prediction/predict`

#### 正常场景（202）

**请求**：

```http
POST /api/v1/wear-prediction/predict HTTP/1.1
Authorization: Bearer ...

{
  "machine_id": "haas-vf2-001",
  "tool_id": "tool-fra-10mm-001",
  "material": "aluminum_6061",
  "cutting_params": {
    "spindle_speed_rpm": 8000,
    "feed_rate_mm_min": 1200,
    "depth_of_cut_mm": 1.5
  },
  "prediction_horizon_hours": 24
}
```

**响应**：

```http
HTTP/1.1 202 Accepted
Location: /api/v1/jobs/wear-pred-20240512-110000-x9y8z7

{
  "code": 0,
  "message": "磨损预测任务已创建",
  "data": {
    "job_id": "wear-pred-20240512-110000-x9y8z7",
    "status": "queued",
    "estimated_duration_seconds": 30
  }
}
```

#### 边界场景：参数超物理范围（400）

**请求**：

```json
{
  "machine_id": "haas-vf2-001",
  "tool_id": "tool-fra-10mm-001",
  "material": "aluminum_6061",
  "cutting_params": {
    "spindle_rpm": 50000,  // 超出机床最大 12000 RPM
    "feed_rate": 1200,
    "depth_of_cut": 1.5
  }
}
```

**响应**：

```http
HTTP/1.1 400 Bad Request

{
  "code": 3004,
  "error_code": "E3004",
  "message": "切削参数超出物理可行范围",
  "detail": "主轴转速 50000 RPM 超出机床 'haas-vf2-001' 的最大转速 12000 RPM",
  "suggestion": "系统已自动调整至 12000 RPM。如需继续使用原参数，请确认机床规格。",
  "adjusted_values": { "spindle_rpm": 12000 },
  "recoverable": true,
  "request_id": "..."
}
```

---

## 5. 异步任务 API

### 5.1 提交异步任务

**端点**：`POST /api/v1/jobs/`

#### 正常场景（202）

**请求**：

```http
POST /api/v1/jobs/ HTTP/1.1
Authorization: Bearer ...

{
  "task_type": "lnn_train",
  "payload": {
    "model_name": "cutting_force",
    "data_path": "/data/train.csv"
  },
  "priority": 5
}
```

**响应**：

```http
HTTP/1.1 202 Accepted
Location: /api/v1/jobs/job-20240512-120000-p1q2r3

{
  "code": 0,
  "message": "任务已提交",
  "data": {
    "job_id": "job-20240512-120000-p1q2r3",
    "status": "pending",
    "created_at": "2024-05-12T12:00:00Z"
  }
}
```

### 5.2 查询任务状态

**端点**：`GET /api/v1/jobs/{job_id}`

#### 正常场景：任务完成（200）

**请求**：

```http
GET /api/v1/jobs/job-20240512-120000-p1q2r3 HTTP/1.1
Authorization: Bearer ...
```

**响应**：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": {
    "job_id": "job-20240512-120000-p1q2r3",
    "task_type": "lnn_train",
    "status": "success",
    "progress": 100,
    "created_at": "2024-05-12T12:00:00Z",
    "started_at": "2024-05-12T12:00:05Z",
    "completed_at": "2024-05-12T12:30:42Z",
    "result": {
      "model_path": "/models/cutting_force_v1.3.0.pt",
      "metrics": {
        "accuracy": 0.94,
        "loss": 0.021,
        "training_time": 1837,
        "epochs_completed": 100
      }
    }
  }
}
```

#### 边界场景：任务不存在（404）

**请求**：

```http
GET /api/v1/jobs/non-existent-job-id HTTP/1.1
```

**响应**：

```http
HTTP/1.1 404 Not Found

{
  "code": 5001,
  "error_code": "E5001",
  "message": "任务未找到",
  "detail": "任务 'non-existent-job-id' 不存在或已过期",
  "suggestion": "请检查任务 ID 是否正确。已完成任务的结果保留 30 天。",
  "recoverable": false,
  "request_id": "..."
}
```

---

## 6. STEP 导入 API

### 6.1 上传 STEP 文件

**端点**：`POST /api/v1/step-import/upload`

#### 正常场景（201）

**请求**：

```http
POST /api/v1/step-import/upload HTTP/1.1
Authorization: Bearer ...
Content-Type: multipart/form-data; boundary=----WebKitFormBoundary

------WebKitFormBoundary
Content-Disposition: form-data; name="file"; filename="flange.step"
Content-Type: application/step

<STEP 文件二进制内容>
------WebKitFormBoundary
Content-Disposition: form-data; name="options"

{"unit": "mm", "auto_detect_coordinate": true, "precision": "high"}
------WebKitFormBoundary--
```

**响应**：

```http
HTTP/1.1 201 Created

{
  "code": 0,
  "message": "STEP 文件上传成功",
  "data": {
    "import_id": "imp-20240512-130000-m1n2o3",
    "file_name": "flange.step",
    "file_size_bytes": 248576,
    "status": "processing",
    "estimated_time_seconds": 15
  }
}
```

#### 边界场景：文件格式错误（415）

**请求**：上传 `.exe` 文件

**响应**：

```http
HTTP/1.1 415 Unsupported Media Type

{
  "code": 1001,
  "error_code": "E1001",
  "message": "STEP 文件解析失败",
  "detail": "文件格式不支持。仅支持 STEP AP203/AP214/AP242、IGES、DXF 格式",
  "suggestion": "请将文件转换为标准 STEP AP214 格式后重新上传。",
  "recoverable": false,
  "request_id": "..."
}
```

#### 边界场景：文件超过大小限制（413）

**请求**：上传 60MB 的 STEP 文件（限制 50MB）

**响应**：

```http
HTTP/1.1 413 Payload Too Large

{
  "code": 1002,
  "error_code": "E1002",
  "message": "文件超过大小限制",
  "detail": "上传文件大小 60.0 MB 超过单文件最大 50 MB 限制",
  "suggestion": "请简化模型或拆分文件后重新上传。可使用 FreeCAD 进行几何简化。",
  "recoverable": false,
  "request_id": "..."
}
```

---

## 7. 仿真 API

### 7.1 启动刀轨仿真

**端点**：`POST /api/v1/simulation/run`

#### 正常场景（202）

**请求**：

```http
POST /api/v1/simulation/run HTTP/1.1
Authorization: Bearer ...

{
  "project_id": "proj-flange-001",
  "precision": "standard",
  "collision_threshold_mm": 2.0,
  "include_stock_definition": true
}
```

**响应**：

```http
HTTP/1.1 202 Accepted

{
  "code": 0,
  "message": "仿真任务已启动",
  "data": {
    "simulation_id": "sim-20240512-140000-d4e5f6",
    "status": "running",
    "estimated_duration_seconds": 120
  }
}
```

#### 边界场景：检测到碰撞（200 + 错误标志）

**响应**（仿真完成后）：

```http
HTTP/1.1 200 OK

{
  "code": 0,
  "message": "仿真完成，但检测到碰撞",
  "data": {
    "simulation_id": "sim-20240512-140000-d4e5f6",
    "status": "completed_with_collision",
    "duration_seconds": 118.4,
    "collision_count": 3,
    "gouging_count": 1,
    "collisions": [
      {
        "tool_path_id": "TP-0023",
        "type": "rapid_move_collision",
        "position": { "x": 25.4, "y": 30.0, "z": 5.0 },
        "severity": "critical"
      }
    ],
    "suggestion": "请调整安全高度至毛坯最高点 + 10mm，或在 G00 前添加 G91 G28 Z0. 回参考点指令"
  }
}
```

---

## 8. 项目管理 API

### 8.1 创建项目

**端点**：`POST /api/v1/projects/`

#### 正常场景（201）

**请求**：

```http
POST /api/v1/projects/ HTTP/1.1
Authorization: Bearer ...

{
  "name": "flange-demo",
  "description": "示例法兰盘项目",
  "workpiece_type": "flange",
  "machine_id": "haas-vf2-001",
  "material": "aluminum_6061",
  "visibility": "team"
}
```

**响应**：

```http
HTTP/1.1 201 Created
Location: /api/v1/projects/proj-20240512-150000-g7h8i9

{
  "code": 0,
  "message": "项目创建成功",
  "data": {
    "id": "proj-20240512-150000-g7h8i9",
    "name": "flange-demo",
    "owner": "engineer01",
    "created_at": "2024-05-12T15:00:00Z",
    "visibility": "team"
  }
}
```

#### 边界场景：项目名重复（409）

**请求**：

```json
{
  "name": "flange-demo",  // 同名项目已存在
  "workpiece_type": "flange",
  "machine_id": "haas-vf2-001"
}
```

**响应**：

```http
HTTP/1.1 409 Conflict

{
  "code": 1002,
  "error_code": "E1002",
  "message": "项目名已存在",
  "detail": "当前用户已有同名项目 'flange-demo'",
  "suggestion": "请使用不同的项目名，或在现有项目中创建新版本。",
  "recoverable": false,
  "request_id": "..."
}
```

---

## 9. 模板市场 API

### 9.1 浏览模板

**端点**：`GET /api/v1/templates/ab-testing/experiments`

#### 正常场景（200）

**请求**：

```http
GET /api/v1/templates/ab-testing/experiments?category=milling&sort=-rating&page=1&size=20 HTTP/1.1
Authorization: Bearer ...
```

**响应**：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": {
    "items": [
      {
        "template_id": "tpl-mill-al-001",
        "name": "6061 铝合金标准铣削模板",
        "category": "milling",
        "rating": 4.8,
        "downloads": 1245,
        "author": "工艺部",
        "version": "1.2.0",
        "updated_at": "2024-04-10T00:00:00Z"
      }
    ],
    "total": 42,
    "page": 1,
    "size": 20
  }
}
```

---

## 10. 健康检查 API

### 10.1 基础健康检查

**端点**：`GET /health`

#### 正常场景（200）

**请求**：

```http
GET /health HTTP/1.1
```

**响应**：

```json
{
  "status": "healthy",
  "environment": "production",
  "version": "2.5.0",
  "components": {
    "database": { "status": "ok", "latency_ms": 5 },
    "cache": { "status": "ok", "latency_ms": 2 },
    "message_queue": { "status": "ok", "latency_ms": 3 }
  },
  "timestamp": 1715520000
}
```

#### 边界场景：详细健康检查（503 降级）

**请求**：

```http
GET /health?detail=true HTTP/1.1
```

**响应**：

```http
HTTP/1.1 503 Service Unavailable

{
  "status": "degraded",
  "environment": "production",
  "version": "2.5.0",
  "components": {
    "database": { "status": "ok", "latency_ms": 8 },
    "cache": { "status": "error", "latency_ms": null, "detail": "Redis connection refused" },
    "message_queue": { "status": "ok", "latency_ms": 12 }
  },
  "timestamp": 1715520000
}
```

---

## 11. 用户主权 API

### 11.1 导出用户数据

**端点**：`POST /api/v1/user-sovereignty/export`

#### 正常场景（202）

**请求**：

```http
POST /api/v1/user-sovereignty/export HTTP/1.1
Authorization: Bearer ...

{
  "scope": ["projects", "activity_logs", "models"],
  "format": "zip"
}
```

**响应**：

```http
HTTP/1.1 202 Accepted

{
  "code": 0,
  "message": "导出任务已创建",
  "data": {
    "export_id": "export-20240512-160000-j1k2l3",
    "status": "preparing",
    "estimated_time_seconds": 60,
    "download_url_expires_at": "2024-05-13T16:00:00Z"
  }
}
```

#### 边界场景：导出范围无效（400）

**请求**：

```json
{
  "scope": ["non_existent_scope"],
  "format": "zip"
}
```

**响应**：

```http
HTTP/1.1 400 Bad Request

{
  "code": 1002,
  "error_code": "E1002",
  "message": "无效的导出范围",
  "detail": "scope 'non_existent_scope' 不存在。可用值: projects, activity_logs, models, snapshots",
  "suggestion": "请从可用值中选择。",
  "recoverable": false,
  "request_id": "..."
}
```

---

## 12. RAG 检索 API

### 12.1 提交检索查询

**端点**：`POST /api/v1/rag/query`

#### 正常场景（200）

**请求**：

```http
POST /api/v1/rag/query HTTP/1.1
Authorization: Bearer ...

{
  "query": "6061 铝合金精加工推荐切削速度",
  "top_k": 5,
  "knowledge_base": "machining_kb"
}
```

**响应**：

```json
{
  "code": 0,
  "message": "检索成功",
  "data": {
    "query": "6061 铝合金精加工推荐切削速度",
    "results": [
      {
        "doc_id": "doc-cnc-001",
        "title": "铝件精加工工艺指南",
        "snippet": "6061 铝合金精加工推荐切削速度 Vc = 200-300 m/min...",
        "score": 0.93,
        "source": "internal_wiki"
      }
    ],
    "model": "bge-large-zh-v1.5"
  }
}
```

#### 边界场景：知识库未配置（503）

**请求**：

```json
{
  "query": "test",
  "knowledge_base": "non_existent_kb"
}
```

**响应**：

```http
HTTP/1.1 503 Service Unavailable

{
  "code": 5004,
  "error_code": "E5004",
  "message": "RAG 服务暂不可用",
  "detail": "知识库 'non_existent_kb' 未配置或未就绪",
  "suggestion": "请联系管理员确认知识库配置。可用知识库：machining_kb, materials_kb, tools_kb",
  "recoverable": false,
  "request_id": "..."
}
```

---

## 下一步

- 查看 [错误码说明](./error-codes.md) 了解所有可能错误
- 使用 Swagger UI（`/api/docs`）进行交互式测试
