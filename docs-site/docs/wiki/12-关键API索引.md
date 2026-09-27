# 12. 关键 API 索引

> 完整 API 由 FastAPI 自动生成，OpenAPI 文档：`/api/docs`（开发环境）。  
> 本章列出按业务域分组的关键端点。

## 12.1 健康与可观测性

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| GET | `/api/health` | 公开 | 深度健康检查（Redis/DB/任务系统） |
| GET | `/api/health/ping` | 公开 | 轻量存活（Docker HEALTHCHECK） |
| GET | `/api/metrics` | 公开 | Prometheus 指标 |
| GET | `/api/v1/logs/stats` | 已登录 | 环形日志统计 |
| GET | `/api/v1/logs/{buffer_type}` | 已登录 | 环形日志分页查询 |
| GET | `/api/v1/heartbeat` | 已登录 | 心跳 |

## 12.2 鉴权与用户

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/v1/auth/register` | 公开 | 注册 |
| POST | `/api/v1/auth/login` | 公开 | 登录 |
| POST | `/api/v1/auth/refresh` | 已登录 | 刷新 Token |
| GET  | `/api/v1/auth/me` | 已登录 | 当前用户信息 |
| POST | `/api/v1/auth/rotate-token` | admin | 轮换 LNN Token |
| GET  | `/api/v1/users` | user:manage | 用户列表 |
| POST | `/api/v1/users` | user:manage | 创建用户 |
| PUT  | `/api/v1/users/{id}` | user:manage | 更新用户 |
| DELETE | `/api/v1/users/{id}` | user:manage | 删除用户 |

## 12.3 LNN 引擎

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/v1/lnn/predict` | 已登录 | 单次预测 |
| POST | `/api/v1/lnn/batch-predict` | 已登录 | 批量预测 |
| POST | `/api/v1/lnn/train` | 已登录 | 提交训练任务 |
| POST | `/api/v1/lnn/quantize` | 已登录 | 量化模型 |
| GET  | `/api/v1/lnn/models` | 已登录 | 已注册模型列表 |
| GET  | `/api/v1/lnn/models/{id}` | 已登录 | 模型详情 |
| POST | `/api/v1/lnn/route` | 已登录 | 任务路由预览 |
| GET  | `/api/v1/lnn/health` | 已登录 | AI 健康 |

## 12.4 异步任务

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/v1/jobs` | 已登录 | 提交任务 |
| GET  | `/api/v1/jobs` | 已登录 | 任务列表 |
| GET  | `/api/v1/jobs/{id}` | 已登录 | 任务详情 |
| POST | `/api/v1/jobs/{id}/cancel` | 已登录 | 取消任务 |
| GET  | `/api/v1/jobs/{id}/stream` | 已登录 | SSE 进度流 |
| POST | `/api/v1/task_checkout` | 已登录 | 签出任务 |
| POST | `/api/v1/task_checkout/release` | 已登录 | 释放签出 |

## 12.5 工程与文件

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/projects` | 已登录 | 创建工程 |
| GET  | `/api/projects` | 已登录 | 工程列表 |
| GET  | `/api/projects/{id}` | 已登录 | 工程详情 |
| PUT  | `/api/projects/{id}` | 已登录 | 更新工程 |
| DELETE | `/api/projects/{id}` | project:delete | 删除工程 |
| POST | `/api/projects/{id}/export` | 已登录 | 导出 |
| POST | `/api/projects/import` | 已登录 | 导入 |

## 12.6 DXF / STEP

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/dxf/parse` | 已登录 | 解析 DXF |
| POST | `/api/dxf/features` | 已登录 | 提取特征 |
| POST | `/api/dxf/to-model` | 已登录 | 转 3D 模型 |
| POST | `/api/dxf/pipeline` | 已登录 | 端到端流水线 |
| POST | `/api/step_import/upload` | 已登录 | STEP 上传 |
| POST | `/api/step_import/parse` | 已登录 | 解析 STEP |
| GET  | `/api/step_import/{id}/model` | 已登录 | 获取 3D 模型 |

## 12.7 工艺规划与规则

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/process_planning/plan` | process:plan | 工艺规划 |
| GET  | `/api/process_planning/{id}` | 已登录 | 规划结果 |
| GET  | `/api/rules` | rule:view | 规则列表 |
| POST | `/api/rules` | rule:edit | 创建规则 |
| PUT  | `/api/rules/{id}` | rule:edit | 更新规则 |
| DELETE | `/api/rules/{id}` | rule:edit | 删除规则 |
| POST | `/api/rules/{id}/test` | rule:edit | 测试规则 |
| GET  | `/api/rule_groups` | rule:view | 规则组 |
| POST | `/api/rule_groups` | rule:edit | 创建规则组 |

## 12.8 后处理 & 仿真

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/postprocessor/generate` | 已登录 | 生成 G 代码 |
| GET  | `/api/postprocessor/controllers` | 已登录 | 控制器列表 |
| POST | `/api/simulation/run` | 已登录 | 运行仿真 |
| GET  | `/api/simulation/{id}/status` | 已登录 | 仿真状态 |
| GET  | `/api/simulation/{id}/result` | 已登录 | 仿真结果 |

## 12.9 RAG & 智能体

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/v1/rag/index` | 已登录 | 建索引 |
| POST | `/api/v1/rag/query` | 已登录 | 检索 |
| POST | `/api/v1/rag/ingest` | 已登录 | 导入文档 |
| GET  | `/api/v1/rag/stats` | 已登录 | 索引统计 |
| GET  | `/api/v1/agent_gateway/agents` | 已登录 | 智能体列表 |
| POST | `/api/v1/agent_gateway/invoke` | 已登录 | 调用智能体 |
| GET  | `/api/v1/agent_gateway/{id}/state` | 已登录 | 智能体状态 |
| GET  | `/api/v1/agent_state` | 已登录 | 智能体总状态 |
| POST | `/api/v1/goal_alignment` | 已登录 | 目标对齐 |
| GET  | `/api/v1/governance/policies` | 已登录 | 治理策略 |
| GET  | `/api/v1/cost_budget` | 已登录 | 成本预算 |

## 12.10 插件 / 技能 / 模板

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| GET  | `/api/v1/plugins` | 已登录 | 已安装插件 |
| POST | `/api/v1/plugins/install` | plugin:install | 安装 |
| DELETE | `/api/v1/plugins/{id}` | plugin:install | 卸载 |
| GET  | `/api/v1/skills` | 已登录 | 技能列表 |
| POST | `/api/v1/skills/{id}/run` | 已登录 | 运行技能 |
| GET  | `/api/v1/templates` | 已登录 | 模板市场 |
| GET  | `/api/v1/templates/{id}` | 已登录 | 模板详情 |
| POST | `/api/v1/templates/{id}/apply` | 已登录 | 应用模板 |
| POST | `/api/v1/templates/{id}/ab` | 已登录 | A/B 测试 |
| POST | `/api/v1/templates/{id}/branch` | 已登录 | 创建分支 |
| POST | `/api/v1/templates/{id}/evolve` | 已登录 | 演化 |
| POST | `/api/v1/templates/{id}/update` | 已登录 | 更新 |
| POST | `/api/v1/pattern_engine/match` | 已登录 | 模式匹配 |

## 12.11 其他业务

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/v1/wear_prediction/predict` | 已登录 | 刀具磨损预测 |
| GET  | `/api/v1/user_sovereignty/me` | 已登录 | 我的数据 |
| POST | `/api/v1/user_sovereignty/export` | 已登录 | 导出我的数据 |
| POST | `/api/v1/user_sovereignty/import` | 已登录 | 导入我的数据 |
| POST | `/api/v1/ollama/chat` | 已登录 | Ollama 对话 |
| POST | `/api/v1/ollama/embeddings` | 已登录 | 嵌入 |
| GET  | `/api/v1/ollama/models` | 已登录 | 模型列表 |
| POST | `/api/v1/llm/invoke` | 已登录 | 通用 LLM 调用 |
| GET  | `/api/v1/process_understanding/*` | 已登录 | 工艺图理解 |
| GET  | `/api/v1/audit` | audit:view | 审计日志查询 |
| GET  | `/api/v1/logs/audit` | audit:view | 审计日志流 |
| POST | `/api/v1/templates/{id}/update/notify` | 已登录 | 模板更新通知 |
| GET  | `/api/v1/version` | 公开 | 后端版本信息 |

## 12.12 鉴权头

所有受保护端点需：
```
Authorization: Bearer <jwt>          # 用户态
Authorization: LNN <token>           # 内部节点
Authorization: Agent <agent_token>  # 智能体
X-Request-ID: <uuid>                 # 可选，便于追踪
```

## 12.13 响应格式

成功：
```json
{ "code": 0, "message": "OK", "data": { ... }, "request_id": "..." }
```

失败：
```json
{
  "code": 40401,
  "message": "Rule not found",
  "data": null,
  "request_id": "...",
  "detail": { ... }
}
```

错误码集中在 `app/core/response.py:ErrorCode`。
