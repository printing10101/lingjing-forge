# API 文档

> 灵境制造 LNN AI API 完整参考文档
> **当前版本**：v2.5.0（与代码版本完全一致）
> **Base URL**：`http://localhost:8765`
> **在线文档**：`http://localhost:8765/api/docs`（Swagger UI） | `http://localhost:8765/api/redoc`（ReDoc）
> **OpenAPI JSON**：`http://localhost:8765/api/openapi.json`

## 文档目录

| 文档 | 用途 |
|------|------|
| [OpenAPI 规范](./openapi.json) | OpenAPI 3.0.3 完整机器可读规范（与代码同步） |
| [请求/响应示例](./examples.md) | 各 API 端点的完整调用示例（正常 + 边界场景） |
| [错误码说明](./error-codes.md) | 完整错误码体系（数值范围、描述、原因、建议） |
| [通用 API 文档](../api-reference.md) | API 端点、请求/响应格式、认证流程总览 |

## 主要端点分组

系统提供 **27 个 API 路由组**，覆盖鉴权、用户、LNN 推理、磨损预测、Agent、模板、规则、仿真、项目管理、STEP 导入等全部业务能力。

### 完整端点列表

| 路由前缀 | 用途 | 典型端点 |
|----------|------|----------|
| `/health`, `/health/live`, `/health/ready`, `/version`, `/api/health/*` | 健康检查 | `GET /health` |
| `/api/v1/auth/*` | 认证 | `POST /api/v1/auth/login` |
| `/api/v1/users/*` | 用户管理 | `GET /api/v1/users/me` |
| `/api/v1/lnn/*` | LNN 推理 | `POST /api/v1/lnn/predict` |
| `/api/v1/lnn/*` | LNN 训练 | `POST /api/v1/lnn/train` |
| `/api/v1/wear-prediction/*` | 磨损预测 | `POST /api/v1/wear-prediction/predict` |
| `/api/v1/jobs/*` | 异步任务 | `POST /api/v1/jobs/` |
| `/api/v1/agent/gateway/*` | Agent 网关 | `POST /api/v1/agent/gateway/process` |
| `/api/v1/agent/state/*` | Agent 状态 | `GET /api/v1/agent/state/{id}` |
| `/api/v1/user-sovereignty/*` | 用户主权 | `GET /api/v1/user-sovereignty/export` |
| `/api/v1/skills/*` | 技能市场 | `GET /api/v1/skills/` |
| `/api/v1/plugins/*` | 插件管理 | `GET /api/v1/plugins/` |
| `/api/v1/cost-budget/*` | 成本预算 | `POST /api/v1/cost-budget/check` |
| `/api/v1/goals/*` | 目标对齐 | `GET /api/v1/goals/` |
| `/api/v1/governance/*` | 治理 | `GET /api/v1/governance/policies` |
| `/api/v1/heartbeat/*` | 心跳 | `POST /api/v1/heartbeat/` |
| `/api/v1/task-checkout/*` | 任务签出 | `POST /api/v1/task-checkout/checkout` |
| `/api/v1/templates/ab-testing/*` | 模板 A/B 测试 | `POST /api/v1/templates/ab-testing/experiments` |
| `/api/v1/templates/branches/*` | 模板分支 | `POST /api/v1/templates/branches/` |
| `/api/v1/templates/evolution/*` | 模板演进 | `GET /api/v1/templates/evolution/history` |
| `/api/v1/templates/updates/*` | 模板更新 | `POST /api/v1/templates/updates/` |
| `/api/v1/pattern-engine/*` | 模式引擎 | `POST /api/v1/pattern-engine/match` |
| `/api/v1/rag/*` | 检索增强 | `POST /api/v1/rag/query` |
| `/api/v1/ollama/*` | Ollama LLM | `POST /api/v1/ollama/generate` |
| `/api/v1/simulation/*` | 仿真 | `POST /api/v1/simulation/run` |
| `/api/v1/projects/*` | 项目管理 | `POST /api/v1/projects/` |
| `/api/v1/step-import/*` | STEP 导入 | `POST /api/v1/step-import/upload` |
| `/api/v1/rules/*` | 工艺规则 | `GET /api/v1/rules/` |
| `/api/v1/process-understanding/*` | 工艺理解 | `POST /api/v1/process-understanding/analyze` |

## 通用约定

### 认证

除 `/health*`、`/version`、`/api/docs`、`/api/redoc`、`/api/openapi.json`、`/api/v1/auth/login`、`/api/v1/auth/register` 外，所有端点均需 JWT 认证：

```
Authorization: Bearer <jwt_token>
```

### 响应格式

**成功响应**：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": { ... }
}
```

**错误响应**：

```json
{
  "code": 1001,
  "error_code": "E1001",
  "message": "资源未找到",
  "severity": "error",
  "request_id": "uuid-string"
}
```

详细字段说明见 [错误码说明](./error-codes.md)。

### 请求追踪

每个请求都会生成 `X-Request-ID`，用于日志关联。客户端可在请求头中传入：

```
X-Request-ID: <client-generated-uuid>
```

### 速率限制

- 默认：100 次/分钟/IP
- 登录：5 次/分钟/IP
- 注册：3 次/小时/IP
- LNN 推理：60 次/分钟/IP
- LNN 训练：5 次/小时/IP

超出限制返回 `429 Too Many Requests`，响应头包含 `Retry-After`。

## 使用工具

| 工具 | 推荐场景 |
|------|----------|
| **Swagger UI**（`/api/docs`） | 交互式测试、查看请求/响应 Schema |
| **ReDoc**（`/api/redoc`） | 阅读完整参考文档 |
| **Postman** | 复杂场景测试、Collection 导入 |
| **curl / HTTPie** | 命令行调试 |
| **OpenAPI Generator** | 客户端 SDK 自动生成 |

## 下一步

- 查看 [请求/响应示例](./examples.md) 学习具体调用方式
- 查看 [错误码说明](./error-codes.md) 了解错误处理
