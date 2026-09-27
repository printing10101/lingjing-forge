# 错误处理与可观测性实施文档

## 概述

本文档描述了灵境制造项目的统一错误处理机制和全链路可追踪能力的实现。

> **P0-18 修复说明**：本文档已与代码实现对齐校验。此前版本存在以下不一致，
> 现已全部修正：
> - ErrorType 枚举：旧文档列 5 种（含不存在的 NETWORK），实际 8 种
> - ErrorSeverity 枚举：旧文档列 LOW/MEDIUM/HIGH/CRITICAL，实际 INFO/WARNING/ERROR/CRITICAL
> - 错误响应格式：旧文档含 `success`/`error_id`/`details` 字段，实际为 `error_code`/`trace_id`/`detail`
> - ErrorContext 类：旧文档声称有 `collect_request_info()` 等方法和独立 `generate_diagnostic_text()` 函数，
>   实际通过 `__init__` 参数收集，方法为 `to_dict()` 和 `to_diagnostic_text()`
> - 前端：旧文档引用 `src/composables/useDiagnostics.ts` 和 `src/components/ErrorNotification.vue`，
>   这两个文件不存在；诊断信息复制功能实际在 `src/components/HealthCheck.vue` 中实现

## 架构设计

### 错误分类体系

系统采用三级错误分类体系：

1. **错误类型 (ErrorType)** — 见 `python/app/core/error_handler.py` 第 43-53 行

   | 枚举值 | 说明 | 错误码范围 |
   |--------|------|-----------|
   | `BUSINESS` | 业务错误 | 1xxx, 4xxx, 7xxx |
   | `SYSTEM` | 系统错误 | 2xxx, 5xxx |
   | `EXTERNAL` | 外部服务错误 | 6xxx |
   | `REPOSITORY` | 数据仓库错误 | 3xxx |
   | `VALIDATION` | 参数校验错误 | 1002 |
   | `AUTH` | 认证授权错误 | 1003/1004 |
   | `MANUFACTURING` | 制造工艺错误 | E1xxx-E4xxx |
   | `UNKNOWN` | 未知错误 | 其他 |

2. **错误严重程度 (ErrorSeverity)** — 见 `python/app/core/error_handler.py` 第 56-62 行

   | 枚举值 | 说明 |
   |--------|------|
   | `INFO` | 信息级，不影响功能 |
   | `WARNING` | 警告级，影响部分功能 |
   | `ERROR` | 错误级，影响核心功能 |
   | `CRITICAL` | 严重错误，系统不可用 |

3. **HTTP 状态码映射**
   - 400: 参数错误、验证失败
   - 401: 认证失败
   - 403: 权限不足
   - 404: 资源不存在
   - 409: 资源冲突
   - 422: 参数验证失败
   - 429: 请求过于频繁
   - 500: 系统内部错误
   - 502: 网关错误
   - 503: 服务不可用
   - 504: 网关超时

### 结构化错误响应格式

所有 API 错误均返回以下标准化格式（见 `build_error_response()` 第 238-247 行）：

```json
{
  "code": 1002,
  "error_code": "E1002",
  "message": "参数错误",
  "error_type": "validation",
  "severity": "warning",
  "timestamp": "2026-06-15T10:30:00.000+00:00",
  "request_id": "req_abc123",
  "trace_id": "req_abc123",
  "path": "/api/v1/...",
  "detail": {
    "field": "email",
    "reason": "invalid_format"
  },
  "suggestion": "请检查邮箱格式",
  "recoverable": false
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `code` | int | 是 | 数值错误码 |
| `error_code` | str | 是 | 字符串错误标识（自动推断，可覆盖） |
| `message` | str | 是 | 用户可读错误消息 |
| `error_type` | str | 是 | 错误分类（见上表） |
| `severity` | str | 是 | 严重程度（info/warning/error/critical） |
| `timestamp` | str | 是 | ISO 8601 格式时间戳 |
| `request_id` | str | 是 | 请求追踪 ID |
| `trace_id` | str | 是 | 链路追踪 ID（与 request_id 相同） |
| `path` | str | 否 | 请求路径 |
| `detail` | any | 否 | 详细错误信息 |
| `suggestion` | str | 否 | 修复建议 |
| `recoverable` | bool | 否 | 是否可自动恢复 |
| `adjusted_values` | dict | 否 | 自动调整后的参数值 |

## 后端实现

### 核心模块

#### 1. error_handler.py

位置：`python/app/core/error_handler.py`

**主要功能：**
- 错误分类和严重程度评估
- 结构化错误响应构建
- 错误上下文收集
- 诊断信息生成
- 增强的错误日志记录

**关键函数与类：**

| 名称 | 类型 | 行号 | 说明 |
|------|------|------|------|
| `ErrorType` | Enum | 43 | 错误大类分类枚举（8 种） |
| `ErrorSeverity` | Enum | 56 | 错误严重程度枚举（4 种） |
| `classify_error_by_code()` | 函数 | 115 | 根据错误码分类 |
| `classify_severity()` | 函数 | 130 | 评估错误严重程度 |
| `get_string_error_code()` | 函数 | 178 | 数值错误码转字符串标识 |
| `build_error_response()` | 函数 | 188 | 构建标准化错误响应 |
| `build_error_response_from_exception()` | 函数 | 270 | 从异常构建错误响应 |
| `ErrorContext` | 类 | 328 | 错误上下文收集器 |
| `log_error()` | 函数 | 432 | 增强的错误日志记录 |

**ErrorContext 类方法：**

`ErrorContext` 通过 `__init__` 参数收集上下文（不使用 `collect_*` 方法），提供：
- `to_dict()`: 转换为字典格式（第 368 行）
- `to_diagnostic_text()`: 生成人类可读的诊断信息文本（第 394 行）

#### 2. 集成点

- `exceptions.py`: 基础异常类定义
- `request_id.py`: 请求 ID 管理
- `api_response.py`: API 响应装饰器
- `exception_handlers.py`: 异常处理器注册

### 使用示例

```python
from app.core.error_handler import (
    build_error_response,
    build_error_response_from_exception,
    ErrorContext,
    log_error,
)

# 从异常构建错误响应
try:
    # 业务逻辑
    pass
except Exception as e:
    response = build_error_response_from_exception(e, path="/api/v1/...")
    log_error(e, context="operation_name")

# 手动构建错误响应（code 为 int 类型）
response = build_error_response(
    code=1002,
    message="参数错误",
    http_status=400,
    detail={"field": "email", "reason": "invalid_format"},
    suggestion="请检查邮箱格式",
)

# 收集错误上下文（通过 __init__ 参数，非 collect_* 方法）
context = ErrorContext(
    error_code=1002,
    message="参数错误",
    path="/api/v1/...",
    http_status=400,
    detail={"field": "email"},
    suggestion="请检查邮箱格式",
    component="auth_service",
    user_action="register",
)
diagnostic_text = context.to_diagnostic_text()  # 用于前端"复制诊断信息"
context_dict = context.to_dict()  # 用于结构化日志
```

## 前端实现

### 核心模块

#### 1. error-handler.ts

位置：`src/utils/error-handler.ts`

**主要功能：**
- 前端错误分类
- 标准化错误对象构建
- 从 Axios 响应和错误提取信息
- 诊断信息收集和复制

**关键函数与类型：**

| 名称 | 类型 | 行号 | 说明 |
|------|------|------|------|
| `ErrorType` | 类型 | 20 | 错误分类联合类型（8 种） |
| `ErrorSeverity` | 类型 | 33 | 严重程度联合类型（info/warning/error/critical） |
| `StandardError` | 接口 | 38 | 标准化错误对象 |
| `DiagnosticContext` | 接口 | 72 | 诊断信息上下文 |
| `classifyErrorByCode()` | 函数 | 141 | 根据错误码分类 |
| `classifySeverity()` | 函数 | 171 | 评估错误严重程度 |
| `getStringErrorCode()` | 函数 | 195 | 数值码转字符串标识 |
| `buildErrorFromResponse()` | 函数 | 206 | 从 Axios 响应构建错误 |
| `buildErrorFromAxiosError()` | 函数 | 251 | 从 Axios 错误构建 |
| `buildErrorFromError()` | 函数 | 278 | 从标准 Error 构建 |
| `isNetworkError()` | 函数 | 303 | 判断是否网络错误 |
| `shouldShowConflictDialog()` | 函数 | 318 | 判断是否需冲突对话框 |
| `toErrorBusPayload()` | 函数 | 332 | 转 ErrorDialogPayload |
| `collectDiagnosticContext()` | 函数 | 355 | 收集诊断上下文 |
| `generateDiagnosticText()` | 函数 | 373 | 生成诊断信息文本 |
| `copyDiagnosticText()` | 函数 | 425 | 复制诊断信息到剪贴板 |
| `registerGlobalErrorHandler()` | 函数 | 468 | 注册全局错误处理器 |
| `triggerGlobalErrorHandlers()` | 函数 | 478 | 触发全局错误处理器 |
| `installGlobalErrorCapture()` | 函数 | 493 | 安装全局错误捕获 |
| `extractErrorMessage()` | 函数 | 535 | 提取错误消息 |
| `formatNetworkError()` | 函数 | 561 | 格式化网络错误 |

> **注意**：旧文档引用的 `src/composables/useDiagnostics.ts`（含 `useDiagnostics()` 和
> `useDiagnosticCopy()`）不存在。诊断信息复制功能直接通过 `copyDiagnosticText()`
> 函数实现，无需组合式函数封装。

#### 2. HealthCheck.vue（诊断信息复制功能实现）

位置：`src/components/HealthCheck.vue`

> **注意**：旧文档引用的 `src/components/ErrorNotification.vue` 不存在。"复制诊断信息"
> 按钮功能实际在 `HealthCheck.vue` 第 62-65 行（模板）和第 303 行
> （`copyDiagnostics` 函数）中实现。

**诊断复制功能：**
- 模板：`@click="copyDiagnostics"` 触发复制
- 逻辑：`copyDiagnostics()` 异步函数（第 303 行）
- 国际化：`healthCheck.copyDiagnostics` 键（见 `src/locales/zh-CN.ts` 和 `en.ts`）

### 使用示例

```typescript
import {
  buildErrorFromAxiosError,
  copyDiagnosticText,
  collectDiagnosticContext,
  generateDiagnosticText,
} from '@/utils/error-handler';

// 在 Axios 拦截器中使用
axios.interceptors.response.use(
  response => response,
  error => {
    const standardError = buildErrorFromAxiosError(error);
    // 处理标准错误对象
    return Promise.reject(standardError);
  }
);

// 收集诊断上下文并生成文本
const ctx = collectDiagnosticContext(error);
const diagnosticText = generateDiagnosticText(ctx);

// 直接复制诊断信息到剪贴板
const success = await copyDiagnosticText(error);
if (success) {
  // 显示成功提示
}
```

## 全链路追踪

### 请求 ID 传播

1. 后端生成唯一请求 ID（通过 `X-Request-ID` 头或自动生成）
2. 请求 ID 包含在所有 API 响应中（`request_id` 和 `trace_id` 字段，值相同）
3. 前端从响应头或响应体提取请求 ID
4. 前端错误对象包含请求 ID 用于追踪

### 错误追踪流程

```
前端错误发生
  ↓
收集错误上下文（包括请求 ID）
  ↓
生成诊断信息（generateDiagnosticText）
  ↓
用户点击"复制诊断信息"（HealthCheck.vue copyDiagnostics）
  ↓
后端记录错误（包含请求 ID，log_error 函数）
  ↓
通过请求 ID 关联前后端错误
```

## 测试覆盖

### 后端测试

位置：`python/tests/test_error_handler.py`

**测试内容：**
- 错误类型分类测试
- 严重程度评估测试
- 错误码映射测试
- 错误响应构建测试
- 错误上下文收集测试
- 日志记录测试

**运行命令：**
```bash
cd python && pytest tests/test_error_handler.py -v
```

### 前端测试

位置：`src/utils/__tests__/error-handler.test.ts`

**测试内容：**
- 错误分类测试
- 严重程度评估测试
- 错误对象构建测试
- 诊断信息生成测试
- 诊断信息复制测试

**运行命令：**
```bash
pnpm test:run -- error-handler
```

## 验收标准

- 所有 API 错误均返回标准化的结构化响应
- 前端错误展示界面包含功能正常的"复制诊断信息"按钮（HealthCheck.vue）
- 单元测试覆盖率达到 80% 以上
- 错误追踪能够贯穿从前端到后端的完整调用链路

## 最佳实践

1. **错误处理原则**
   - 所有错误都应该被捕获和处理
   - 不要暴露敏感信息给客户端（使用 `safe_error_message()`）
   - 提供有意义的错误消息
   - 记录详细的错误上下文

2. **日志记录原则**
   - 使用适当的日志级别
   - 包含请求 ID 用于追踪
   - 记录足够的上下文信息
   - 避免记录敏感数据（LogSanitizer 已集成到 SensitiveDataFilter）

3. **用户体验原则**
   - 提供友好的错误提示
   - 允许用户复制诊断信息
   - 根据错误严重程度采取不同措施
   - 提供错误恢复建议（`suggestion` 字段）

## 未来改进

1. 集成错误监控系统（如 Sentry）
2. 实现错误聚合和分析
3. 添加错误恢复建议
4. 实现错误统计仪表板
5. 支持错误重试机制
