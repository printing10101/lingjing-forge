# API 参考文档

> **自动生成**: 本文档由 `scripts/gen-api-docs.py` 自动生成
> 
> **最后更新**: 自动填充
> 
> **适用版本**: 灵境制造平台 v2.7.0

> **交叉引用**: 本文档为 API 端点总览，完整的请求/响应示例、错误码详解与认证流程见 [`docs/api/README.md`](./api/README.md)。两份文档遵循同一响应格式约定（见下文"响应格式约定"小节）。

## 概述

灵境制造平台提供 RESTful API 接口，支持 AI 模型管理、刀具磨损预测、训练任务管理等功能。

### 基础信息

| 属性 | 值 |
|------|-----|
| 基础路径 | `/api/v1` |
| 数据格式 | JSON |
| 字符编码 | UTF-8 |

### 通用请求格式

所有 POST/PUT 请求的请求体必须使用 JSON 格式，并设置请求头：

```
Content-Type: application/json
```

### 响应格式约定

所有 API 响应遵循统一格式，字段定义与 `python/app/core/response.py` 实现保持一致：

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | `number` | 数值状态码，`0` 表示成功，非 `0` 表示错误（如 `1001`、`2001`） |
| `message` | `string` | 人类可读的状态描述 |
| `data` | `any` | 成功时为业务数据，错误时通常省略或为 `null` |
| `request_id` | `string` | 请求追踪标识，对应客户端 `X-Request-ID` |

> **注意**：代码内部 `ErrorCode` 保留字符串枚举（如 `SUCCESS`、`NOT_FOUND`）以保持向后兼容，但通过 `code_to_numeric()` 映射表统一转换为数值后返回给客户端。客户端应始终以数值 `code` 判断响应状态，不应依赖字符串枚举值。

### 通用响应格式

成功响应示例：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": { ... },
  "request_id": "uuid-string"
}
```

错误响应示例：

```json
{
  "code": 1001,
  "message": "资源未找到",
  "request_id": "uuid-string",
  "detail": "附加详情（可选）",
  "suggestion": "建议操作（可选）"
}
```

## 认证与授权

当前版本 API 无需额外认证。生产环境部署时建议启用 API Key 或 JWT Token 认证机制。

## 错误码参考

下表列出 `ErrorCode` 枚举与对应数值码的映射关系。客户端应以 `code`（数值）列为准。

| `ErrorCode` 枚举 | `code`（数值） | 说明 | 解决建议 |
|------------------|---------------|------|----------|
| `SUCCESS` | `0` | 操作成功 | - |
| `NOT_FOUND` | `1001` | 资源未找到 | 检查请求路径或资源标识是否正确 |
| `INVALID_REQUEST` | `1002` | 请求参数错误 | 检查请求参数格式和取值范围 |
| `UNAUTHORIZED` | `1003` | 未授权访问 | 检查认证凭据是否有效 |
| `FILE_NOT_FOUND` | `1008` | 文件不存在 | 检查文件路径是否正确 |
| `INTERNAL_ERROR` | `2001` | 服务器内部错误 | 检查服务器日志，联系技术支持 |
| `SERVICE_UNAVAILABLE` | `2002` | 服务不可用 | 检查服务状态，稍后重试 |
| `CAD_GENERATION_ERROR` | `7001` | CAD 生成失败 | 检查输入参数和模板配置 |

## 全部 API 路由

所有已注册路由的完整清单（按路径前缀分组）。

### /agents

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/agents/` |  |

### /agents/{agent_id}

| 方法 | 路径 | 说明 |
|------|------|------|
| `DELETE` | `/agents/{agent_id}` |  |
| `GET` | `/agents/{agent_id}` |  |
| `GET` | `/agents/{agent_id}/checkpoints` |  |
| `POST` | `/agents/{agent_id}/checkpoints/cleanup` |  |
| `POST` | `/agents/{agent_id}/checkpoints/rollback` |  |
| `POST` | `/agents/{agent_id}/checkpoints/save` |  |
| `POST` | `/agents/{agent_id}/clone` |  |
| `POST` | `/agents/{agent_id}/context/update` |  |
| `POST` | `/agents/{agent_id}/deploy` | 部署 Agent：加载状态、标记部署时间并将状态切换为 busy（真实状态变更）。 |
| `POST` | `/agents/{agent_id}/heartbeat/start` |  |
| `POST` | `/agents/{agent_id}/heartbeat/stop` |  |
| `GET` | `/agents/{agent_id}/history` |  |
| `POST` | `/agents/{agent_id}/memory/add` |  |
| `POST` | `/agents/{agent_id}/memory/prune` |  |
| `POST` | `/agents/{agent_id}/resume` |  |
| `POST` | `/agents/{agent_id}/rollback` |  |
| `POST` | `/agents/{agent_id}/save` |  |
| `POST` | `/agents/{agent_id}/snapshot` |  |

### /api/chatter

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chatter/modal/identify` | 基于频响函数曲线辨识多模态参数。 |
| `POST` | `/api/chatter/modal/upload` | 上传锤击测试 FRF 数据文件（CSV/JSON）。 |
| `POST` | `/api/chatter/predict` | 单点稳定性预测。 |
| `POST` | `/api/chatter/sld` | 生成稳定性叶图（SLD）可视化数据。 |

### /api/cutting-force

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/cutting-force/adaptive/preview` | 用默认参数演示求解器效果。 |
| `POST` | `/api/cutting-force/adaptive/solve-segment` | 单段刀路自适应求解。 |
| `POST` | `/api/cutting-force/adaptive/solve-segments` | 批量多段刀路自适应求解。 |
| `GET` | `/api/cutting-force/kienzle/coefficients/{material}` | 查询材料的 Kienzle 系数。 |
| `POST` | `/api/cutting-force/kienzle/compute` | 正向 Kienzle 切削力计算。 |

### /api/health

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 标准化主健康检查 |
| `GET` | `/api/health` | Health check endpoint for the test server. |
| `GET` | `/api/health/ping` | 轻量级健康探针 |
| `GET` | `/api/health/ping` | Lightweight ping endpoint. |

### /api/import

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/import/step` | 导入STEP文件并进行解析和格式转换。 |
| `POST` | `/api/import/step/` | 导入STEP文件并进行解析和格式转换。 |
| `DELETE` | `/api/import/step/cache` | 清空STEP解析缓存。 |
| `GET` | `/api/import/step/cache/stats` | 获取STEP解析缓存统计信息。 |
| `GET` | `/api/import/step/history` | 获取STEP导入历史记录。 |
| `DELETE` | `/api/import/step/history/{file_name}` | 删除指定导入文件。 |
| `GET` | `/api/import/step/output/{file_name}` | 获取转换后的输出文件(STL/BREP)。 |

### /api/ollama

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ollama/models` | 获取 Ollama 已安装模型列表 |
| `GET` | `/api/ollama/status` | 获取 Ollama 服务状态 |

### /api/projects

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/projects/download/{project_name}` | 下载工程文件。 |
| `GET` | `/api/projects/list` | 列出所有工程文件。 |
| `POST` | `/api/projects/new` | 创建新的空白工程。 |
| `POST` | `/api/projects/open` | 打开 .ljm 工程文件。 |
| `POST` | `/api/projects/save` | 保存工程为 .ljm 文件。 |
| `POST` | `/api/projects/save-as` | 另存为工程文件。 |
| `POST` | `/api/projects/upload-resource` | 上传资源文件到临时目录。 |
| `DELETE` | `/api/projects/{project_name}` | 删除指定的工程文件。 |

### /api/rag

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/rag/add` |  |
| `POST` | `/api/rag/backup/export` |  |
| `POST` | `/api/rag/backup/import` |  |
| `POST` | `/api/rag/import/file` |  |
| `GET` | `/api/rag/list` |  |
| `POST` | `/api/rag/load/default` |  |
| `POST` | `/api/rag/load/json` |  |
| `POST` | `/api/rag/maintenance/cleanup` |  |
| `POST` | `/api/rag/maintenance/optimize` |  |
| `POST` | `/api/rag/process/add` | 添加工艺四元组到索引。 |
| `GET` | `/api/rag/process/features` | 列出所有已建模的特征类型。 |
| `POST` | `/api/rag/process/flush` | 强制将工艺四元组索引落盘。 |
| `POST` | `/api/rag/process/recommend` | 根据加工特征推荐工艺方案（CAMWorks TechDB 式自动决策）。 |
| `POST` | `/api/rag/process/related-documents` | 集成点 4：通过 chunk_ids + EntityIndex 反向查询原始文档。 |
| `POST` | `/api/rag/process/seed` | 注入默认工艺知识库（覆盖常见特征的典型工艺方案）。 |
| `POST` | `/api/rag/process/similar` | 查找相似工艺记录（3 层匹配：精确 / 同特征 / 材料迁移）。 |
| `GET` | `/api/rag/process/stats` | 获取工艺四元组索引统计信息。 |
| `GET` | `/api/rag/process/{feature}/processes` | 获取指定特征对应的所有工艺方法。 |
| `GET` | `/api/rag/query` | RAG 知识库查询（v2 增强）。 |
| `GET` | `/api/rag/search` |  |
| `DELETE` | `/api/rag/source/{source}` |  |
| `GET` | `/api/rag/stats` |  |
| `POST` | `/api/rag/v2/ablation` | 运行 ablation study，逐项关闭增强模块，量化各模块贡献。 |
| `DELETE` | `/api/rag/v2/cache` | 清空检索结果 LRU 缓存。 |
| `GET` | `/api/rag/v2/cache/stats` | 获取检索结果 LRU 缓存的命中统计。 |
| `POST` | `/api/rag/v2/comparison` | 生成 baseline vs enhanced A/B 对比报告。 |
| `GET` | `/api/rag/v2/enhancement/status` | 获取 RAG 增强模块的实时状态与性能指标。 |
| `POST` | `/api/rag/v2/evaluation` | 运行检索质量评估。 |
| `POST` | `/api/rag/v2/signal-fusion/retrieve` | 集成点 2：通过 RagRetrievalEngine 委托 SignalFusionKnowledgeBase 检索。 |
| `DELETE` | `/api/rag/{doc_id}` |  |

### /api/rules

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/rules/backup` |  |
| `POST` | `/api/rules/create` |  |
| `DELETE` | `/api/rules/delete/{rule_id}` |  |
| `GET` | `/api/rules/detail/{rule_id}` |  |
| `GET` | `/api/rules/export` |  |
| `POST` | `/api/rules/groups/create` |  |
| `DELETE` | `/api/rules/groups/delete/{group_id}` |  |
| `GET` | `/api/rules/groups/list` |  |
| `PUT` | `/api/rules/groups/update/{group_id}` |  |
| `POST` | `/api/rules/import` |  |
| `GET` | `/api/rules/list` |  |
| `GET` | `/api/rules/preview` |  |
| `GET` | `/api/rules/stats` |  |
| `PUT` | `/api/rules/update/{rule_id}` |  |

### /api/simulation

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/simulation/auto-diff/compare` | 比对设计模型与仿真切削结果，识别过切与残料。 |
| `GET` | `/api/simulation/auto-diff/{task_id}` | 按 task_id 查询 Auto-Diff 比对结果。 |
| `POST` | `/api/simulation/check-conflict` | Check tool-slot diameter compatibility. |
| `POST` | `/api/simulation/export-animation` | Export simulation animation as GIF or MP4. |
| `POST` | `/api/simulation/factory/closed-loop` | 运行仿真工厂闭环生产（感知→决策→执行→反馈），返回 NLDF 风格 KPI 评分。 |
| `GET` | `/api/simulation/factory/demo-status` | 返回仿真演示设备清单（Phase 2 demo registry）。 |
| `POST` | `/api/simulation/fem/solve` | 简化 FEM 求解（简支梁三点弯曲解析解）。 |
| `GET` | `/api/simulation/history` | Query simulation history records. |
| `GET` | `/api/simulation/output/{filename}` | Serve simulation output STL file. |
| `DELETE` | `/api/simulation/result/{task_id}` | Delete a simulation result from cache and disk. |
| `POST` | `/api/simulation/run` | Run voxel cutting simulation synchronously. |
| `POST` | `/api/simulation/run/async` | Start voxel cutting simulation asynchronously. |
| `GET` | `/api/simulation/status/{task_id}` | Query simulation task status and results. |

### /api/v1

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/activity/brief` | 首页活动简报：今日生产 / 任务 / 告警 / 质量统计（真实聚合）。 |
| `POST` | `/api/v1/admin/shutdown` | 触发后端优雅关闭（由 Tauri Rust 端调用）。 |
| `POST` | `/api/v1/auth/guest` | 访客模式登录：无需注册即可获得临时访问身份。 |
| `POST` | `/api/v1/auth/login` |  |
| `POST` | `/api/v1/auth/logout` |  |
| `GET` | `/api/v1/auth/me` |  |
| `POST` | `/api/v1/auth/refresh` |  |
| `POST` | `/api/v1/auth/register` | 注册新用户。 |
| `GET` | `/api/v1/cam-validation/precision_info` | 查询当前精度档位信息、可用 CAM 后端与工业硬门槛（不创建任务）。 |
| `GET` | `/api/v1/cam-validation/tasks` | 列出最近任务 |
| `POST` | `/api/v1/cam-validation/tasks` | 创建 CAM 校验任务 |
| `DELETE` | `/api/v1/cam-validation/tasks/{task_id}` | 取消/删除任务 |
| `GET` | `/api/v1/cam-validation/tasks/{task_id}` | 查询任务状态 |
| `POST` | `/api/v1/cam-validation/tasks/{task_id}/confirm` | 确认任务（REVIEWED → SUCCEEDED + 导出 cam_report + internal_report JSON） |
| `GET` | `/api/v1/cam-validation/tasks/{task_id}/internal_report/download` | 下载内部预校验详细报告 JSON |
| `GET` | `/api/v1/cam-validation/tasks/{task_id}/report/download` | 下载 CAM 校验报告 JSON |
| `GET` | `/api/v1/cam-validation/tasks/{task_id}/result` | 获取 CAM 校验结果列表 + 审核状态 |
| `POST` | `/api/v1/cam-validation/tasks/{task_id}/review` | 工程师审核单个特征的 CAM 校验结果 |
| `POST` | `/api/v1/cam-validation/tasks/{task_id}/run` | 异步触发 CAM 校验流水线执行 |
| `GET` | `/api/v1/chatter_prediction/precision_info` | 查询当前精度档位信息、LTC 模型可用性与工业硬门槛（不创建任务）。 |
| `GET` | `/api/v1/chatter_prediction/tasks` | 列出最近任务 |
| `POST` | `/api/v1/chatter_prediction/tasks` | 创建颤振预测任务 |
| `DELETE` | `/api/v1/chatter_prediction/tasks/{task_id}` | 取消/删除任务 |
| `GET` | `/api/v1/chatter_prediction/tasks/{task_id}` | 查询任务状态 |
| `GET` | `/api/v1/chatter_prediction/tasks/{task_id}/chatter_report/download` | 下载 ChatterReport JSON 文件 |
| `POST` | `/api/v1/chatter_prediction/tasks/{task_id}/export` | 导出 ChatterReport JSON（供阶段 6 G 代码生成） |
| `GET` | `/api/v1/chatter_prediction/tasks/{task_id}/result` | 获取颤振预测结果列表 + 审核状态 |
| `POST` | `/api/v1/chatter_prediction/tasks/{task_id}/review` | 工程师审核单个特征的颤振预测结果 |
| `POST` | `/api/v1/chatter_prediction/tasks/{task_id}/run` | 异步触发颤振预测流水线执行 |
| `POST` | `/api/v1/collision-check` | 执行碰撞检测。 |
| `GET` | `/api/v1/collision-check/health` | 碰撞检测服务健康检查。 |
| `POST` | `/api/v1/cost-budget/adjust-budget` |  |
| `GET` | `/api/v1/cost-budget/adjustment-history` |  |
| `GET` | `/api/v1/cost-budget/alerts` |  |
| `POST` | `/api/v1/cost-budget/alerts/read-all` |  |
| `DELETE` | `/api/v1/cost-budget/alerts/{alert_id}` |  |
| `POST` | `/api/v1/cost-budget/alerts/{alert_id}/read` |  |
| `POST` | `/api/v1/cost-budget/check` |  |
| `POST` | `/api/v1/cost-budget/check-cascade` |  |
| `POST` | `/api/v1/cost-budget/enforce` |  |
| `GET` | `/api/v1/cost-budget/enforcement-log` |  |
| `GET` | `/api/v1/cost-budget/policies` |  |
| `POST` | `/api/v1/cost-budget/policies` |  |
| `POST` | `/api/v1/cost-budget/reset` |  |
| `GET` | `/api/v1/cost-budget/reset-log` |  |
| `GET` | `/api/v1/cost-budget/suggestions` |  |
| `GET` | `/api/v1/cost-budget/summary` |  |
| `GET` | `/api/v1/cost-budget/task/{task_id}` |  |
| `GET` | `/api/v1/cost-budget/trend` |  |
| `GET` | `/api/v1/cost-budget/unit-prices` |  |
| `POST` | `/api/v1/cost-budget/unit-prices` |  |
| `GET` | `/api/v1/cutting_parameters/precision_info` | 查询当前精度档位信息、材料列表与工业硬门槛（不创建任务）。 |
| `GET` | `/api/v1/cutting_parameters/tasks` | 列出最近任务 |
| `POST` | `/api/v1/cutting_parameters/tasks` | 创建切削参数推荐任务 |
| `DELETE` | `/api/v1/cutting_parameters/tasks/{task_id}` | 取消/删除任务 |
| `GET` | `/api/v1/cutting_parameters/tasks/{task_id}` | 查询任务状态 |
| `GET` | `/api/v1/cutting_parameters/tasks/{task_id}/chatter_params/download` | 下载 ChatterParams JSON 文件 |
| `POST` | `/api/v1/cutting_parameters/tasks/{task_id}/export` | 导出 ChatterParams JSON（供阶段 5 颤振预测） |
| `GET` | `/api/v1/cutting_parameters/tasks/{task_id}/result` | 获取推荐参数列表 + 审核状态 |
| `POST` | `/api/v1/cutting_parameters/tasks/{task_id}/review` | 工程师审核单个特征的切削参数 |
| `POST` | `/api/v1/cutting_parameters/tasks/{task_id}/run` | 异步触发切削参数推荐流水线执行 |
| `GET` | `/api/v1/datasets` | List datasets with pagination. |
| `POST` | `/api/v1/datasets` | Create a dataset. |
| `POST` | `/api/v1/datasets/lineage` | Record lineage. |
| `GET` | `/api/v1/datasets/lineage/{target_uri}` | Query lineage graph. |
| `GET` | `/api/v1/datasets/metrics` | Get global dataset metrics. |
| `GET` | `/api/v1/datasets/{dataset_id}` | Get dataset details. |
| `POST` | `/api/v1/datasets/{dataset_id}/commit` | Commit a new version. |
| `POST` | `/api/v1/datasets/{dataset_id}/deprecate` | Deprecate a version. |
| `GET` | `/api/v1/datasets/{dataset_id}/versions` | List all versions. |
| `GET` | `/api/v1/datasets/{dataset_id}/versions/{version}` | Get specific version details. |
| `GET` | `/api/v1/datasets/{dataset_id}/versions/{version}/read` | Stream version contents as JSONL. |
| `GET` | `/api/v1/dnc/machines` | 列出已连接机床 |
| `POST` | `/api/v1/dnc/machines` | 添加机床连接 |
| `DELETE` | `/api/v1/dnc/machines/{machine_id}` | 移除机床连接 |
| `GET` | `/api/v1/dnc/machines/{machine_id}/alarms` | 获取机床报警（仅 MTConnect；OPC UA 未接入返回 SERVICE_UNAVAILABLE 错误体） |
| `GET` | `/api/v1/dnc/machines/{machine_id}/status` | 获取机床状态 |
| `POST` | `/api/v1/dnc/nc-program/send` | 发送 NC 程序到机床 |
| `GET` | `/api/v1/dnc/status` | 获取所有机床状态 |
| `POST` | `/api/v1/dnc/unified/connect` | 自动探测连接（双协议） |
| `POST` | `/api/v1/dnc/unified/discover` | 扫描局域网内机床 |
| `DELETE` | `/api/v1/dnc/unified/{machine_id}` | 断开统一适配器 |
| `GET` | `/api/v1/dnc/unified/{machine_id}/info` | 获取适配器运行信息 |
| `GET` | `/api/v1/dnc/unified/{machine_id}/status` | 获取统一状态 |
| `GET` | `/api/v1/documents/` | 获取文档列表，支持按分类、状态、关键词筛选。 |
| `POST` | `/api/v1/documents/` | 创建文档。 |
| `GET` | `/api/v1/documents/categories/` | 获取所有分类及其文档数量。 |
| `POST` | `/api/v1/documents/seed` | 填充知识库文档演示数据。 |
| `DELETE` | `/api/v1/documents/{doc_id}` | 删除文档。 |
| `GET` | `/api/v1/documents/{doc_id}` | 获取单个文档详情（浏览量+1）。 |
| `PUT` | `/api/v1/documents/{doc_id}` | 更新文档。 |
| `GET` | `/api/v1/dreaming/learnings` | 「它这周学会了什么」：窗口期内新增规则草稿与灰度事件流。 |
| `POST` | `/api/v1/dreaming/reflect` | 触发完整离线反思（提取→反思→合成→报告，产物一律 draft 状态）。 |
| `GET` | `/api/v1/dreaming/rules` | 规则草稿 × 灰度发布状态联查。 |
| `POST` | `/api/v1/dreaming/rules/{rule_id}/promote` | 灰度晋级（SHADOW→CANARY→FULL，FULL 强制双重沙箱校验）。 |
| `POST` | `/api/v1/dreaming/rules/{rule_id}/publish` | 人工放行：草稿进入灰度 SHADOW（AI 起草，人确认）。 |
| `POST` | `/api/v1/dreaming/rules/{rule_id}/rollback` | 一键回滚（经 RollbackManager，含冷却期与历史留痕）。 |
| `GET` | `/api/v1/dreaming/status` | 子系统状态（阶段分布 / 草稿数 / 最近反思）。 |
| `POST` | `/api/v1/dynamic-adjustment/calibrate-wear` | 使用实时传感器数据 EWMA 校正磨损预测。 |
| `POST` | `/api/v1/dynamic-adjustment/closed-loop` | 端到端闭环：磨损 → 决策 → NC 改写（单次调用完成全链路）。 |
| `POST` | `/api/v1/dynamic-adjustment/decide` | 根据刀具磨损状态生成切削参数调整决策。 |
| `GET` | `/api/v1/dynamic-adjustment/health` | 动态调参闭环模块健康检查。 |
| `POST` | `/api/v1/dynamic-adjustment/rewrite-nc` | 按调整决策改写 NC 代码中的主轴转速与进给速度。 |
| `POST` | `/api/v1/embedding/encode` | 三模态特征 → 512 维统一制造语义嵌入（纯 NumPy 投影，零模型加载）。 |
| `POST` | `/api/v1/embedding/index` | 为指定层（cognitive/perception/execution）构建 kd-tree ANN 索引。 |
| `POST` | `/api/v1/embedding/query` | 跨层检索（支持语义轴加权 axis_weights 与模态过滤）。 |
| `GET` | `/api/v1/embedding/status` | 已建索引统计（层名/规模/维度）。 |
| `GET` | `/api/v1/equipment` | 获取设备列表，可按状态过滤并分页。 |
| `GET` | `/api/v1/equipment/alarms/` | 获取告警列表，支持多条件过滤和分页。 |
| `PUT` | `/api/v1/equipment/alarms/{alarm_id}/status` | 更新告警状态。 |
| `GET` | `/api/v1/equipment/maintenance/` | 获取维护计划列表，支持过滤和分页。 |
| `PUT` | `/api/v1/equipment/maintenance/{plan_id}` | 更新维护计划。 |
| `POST` | `/api/v1/equipment/seed` | 初始化设备监控演示数据（6台设备、6条告警、6条维护计划）。 |
| `GET` | `/api/v1/equipment/stats/` | 获取设备统计信息。 |
| `GET` | `/api/v1/equipment/{equipment_id}` | 获取单台设备详情及当前指标。 |
| `PUT` | `/api/v1/equipment/{equipment_id}` | 更新设备状态和指标。 |
| `GET` | `/api/v1/explainability/` | 分页列出历史解释记录（支持 explanation_type / model_uri 过滤）. |
| `POST` | `/api/v1/explainability/compare` | 对比两个解释（生成差异 payload）. |
| `POST` | `/api/v1/explainability/confidence` | 生成置信度分布解释（MC dropout 采样）. |
| `POST` | `/api/v1/explainability/counterfactual` | 生成反事实解释. |
| `POST` | `/api/v1/explainability/gate-dynamics` | 生成门控动力学解释. |
| `POST` | `/api/v1/explainability/hidden-state` | 生成隐状态投影解释. |
| `DELETE` | `/api/v1/explainability/{explanation_id}` | 删除解释记录（同时删除 payload 文件）. |
| `GET` | `/api/v1/explainability/{explanation_id}` | 查询解释详情. |
| `GET` | `/api/v1/feature_extraction/precision_info` | 查询当前精度档位信息与工业硬门槛（不创建任务）。 |
| `GET` | `/api/v1/feature_extraction/tasks` | 列出最近任务 |
| `POST` | `/api/v1/feature_extraction/tasks` | 通过 mesh 路径创建特征提取任务（链路模式） |
| `POST` | `/api/v1/feature_extraction/tasks/upload` | 通过上传 mesh 文件创建特征提取任务（外部导入模式） |
| `DELETE` | `/api/v1/feature_extraction/tasks/{task_id}` | 删除任务（清理 workspace） |
| `GET` | `/api/v1/feature_extraction/tasks/{task_id}` | 查询任务状态 |
| `GET` | `/api/v1/feature_extraction/tasks/{task_id}/export` | 导出已确认特征集为 JSON（供阶段 3 参数化 STEP 生成使用） |
| `GET` | `/api/v1/feature_extraction/tasks/{task_id}/export/download` | 下载已导出的特征集 JSON 文件 |
| `GET` | `/api/v1/feature_extraction/tasks/{task_id}/result` | 获取已提取的特征列表 |
| `POST` | `/api/v1/feature_extraction/tasks/{task_id}/review` | 工程师审核单个特征（人工介入核心端点） |
| `POST` | `/api/v1/feature_extraction/tasks/{task_id}/run` | 异步触发特征提取执行 |
| `POST` | `/api/v1/evolution/loop/run` | 手动触发一次演化循环（统计→提示词补丁提案→报告；提案制不自动发布，需 governance:write） |
| `GET` | `/api/v1/evolution/proposals` | 列出提示词候选/应用版本记录（proposed/applied/rejected/rolled_back） |
| `POST` | `/api/v1/evolution/proposals/{prompt_id}/{version}/promote` | 候选版本门控发布：热更新上线 → replay 回归门控 → FAIL 自动回滚 |
| `POST` | `/api/v1/evolution/proposals/{prompt_id}/{version}/reject` | 拒绝提案（候选不进线上注册表，仅留痕） |
| `POST` | `/api/v1/evolution/proposals/{prompt_id}/{version}/rollback` | 回滚已应用的提示词版本（线上立即回到剩余最高版本） |
| `GET` | `/api/v1/evolution/reports` | 最近一份进化报告内容与报告文件列表 |
| `GET` | `/api/v1/evolution/stats` | 失败案例库统计、失败 TOP 类别与提示词版本总览 |
| `GET` | `/api/v1/flywheel/definitions` | 获取指标定义说明 |
| `GET` | `/api/v1/flywheel/deployments` | 获取模型热更新部署记录 |
| `POST` | `/api/v1/flywheel/feedback` | 提交用户反馈到飞轮反馈数据集（annotation/adoption/correction；自进化 M0 反馈回流入口，需 dataset:write） |
| `GET` | `/api/v1/flywheel/metrics` | 获取飞轮指标详情（含历史数据） |
| `GET` | `/api/v1/flywheel/report/weekly` | 生成每周飞轮报告 |
| `GET` | `/api/v1/flywheel/status` | 获取飞轮当前状态 |
| `POST` | `/api/v1/flywheel/synthetic/generate` | W4.1 仿真合成数据：体素校验+切削力模型参数扫描，生成「参数→仿真结果」样本对并提交为带血缘的不可变数据集版本。 |
| `GET` | `/api/v1/gcode-generation/precision_info` | 查询当前精度档位信息、控制器类型与工业硬门槛（不创建任务）。 |
| `GET` | `/api/v1/gcode-generation/tasks` | 列出最近任务 |
| `POST` | `/api/v1/gcode-generation/tasks` | 创建 G 代码生成任务 |
| `DELETE` | `/api/v1/gcode-generation/tasks/{task_id}` | 取消/删除任务 |
| `GET` | `/api/v1/gcode-generation/tasks/{task_id}` | 查询任务状态 |
| `POST` | `/api/v1/gcode-generation/tasks/{task_id}/confirm` | 确认任务（REVIEWED → SUCCEEDED + 导出 G 代码 + 报告 JSON） |
| `GET` | `/api/v1/gcode-generation/tasks/{task_id}/gcode/download` | 下载 G 代码文件 |
| `GET` | `/api/v1/gcode-generation/tasks/{task_id}/report/download` | 下载审核记录 JSON |
| `GET` | `/api/v1/gcode-generation/tasks/{task_id}/result` | 获取 G 代码生成结果列表 + 审核状态 |
| `POST` | `/api/v1/gcode-generation/tasks/{task_id}/review` | 工程师审核单个特征的 G 代码段 |
| `POST` | `/api/v1/gcode-generation/tasks/{task_id}/run` | 异步触发 G 代码生成流水线执行 |
| `GET` | `/api/v1/goal-alignment/goals` |  |
| `POST` | `/api/v1/goal-alignment/goals` |  |
| `GET` | `/api/v1/goal-alignment/goals/tree` |  |
| `DELETE` | `/api/v1/goal-alignment/goals/{goal_id}` |  |
| `GET` | `/api/v1/goal-alignment/goals/{goal_id}` |  |
| `PUT` | `/api/v1/goal-alignment/goals/{goal_id}` |  |
| `GET` | `/api/v1/goal-alignment/goals/{goal_id}/chain` |  |
| `GET` | `/api/v1/goal-alignment/goals/{goal_id}/children` |  |
| `GET` | `/api/v1/goal-alignment/goals/{goal_id}/history` |  |
| `GET` | `/api/v1/goal-alignment/goals/{goal_id}/progress` |  |
| `POST` | `/api/v1/goal-alignment/goals/{goal_id}/propagate` |  |
| `GET` | `/api/v1/goal-alignment/progress/all` |  |
| `POST` | `/api/v1/goal-alignment/scan` |  |
| `GET` | `/api/v1/goal-alignment/summary` |  |
| `POST` | `/api/v1/goal-alignment/tasks` |  |
| `GET` | `/api/v1/goal-alignment/tasks/{task_id}/alignment` |  |
| `GET` | `/api/v1/goal-alignment/tasks/{task_id}/context` |  |
| `POST` | `/api/v1/goal-alignment/tasks/{task_id}/status` |  |
| `GET` | `/api/v1/governance/approval-dashboard` |  |
| `GET` | `/api/v1/governance/approval-requests` |  |
| `POST` | `/api/v1/governance/approval-requests` |  |
| `GET` | `/api/v1/governance/approval-requests/my` |  |
| `GET` | `/api/v1/governance/approval-requests/{request_id}` |  |
| `POST` | `/api/v1/governance/approval-requests/{request_id}/assign` |  |
| `POST` | `/api/v1/governance/approval-requests/{request_id}/decide` |  |
| `POST` | `/api/v1/governance/approval-requests/{request_id}/escalate` |  |
| `POST` | `/api/v1/governance/approval-timeout-handler` |  |
| `GET` | `/api/v1/governance/audit-log/export` |  |
| `GET` | `/api/v1/governance/delegations` |  |
| `POST` | `/api/v1/governance/delegations` |  |
| `POST` | `/api/v1/governance/emergency-override` |  |
| `POST` | `/api/v1/governance/emergency-retroactive-approval` |  |
| `GET` | `/api/v1/governance/reports/governance` |  |
| `POST` | `/api/v1/governance/risk-assess` |  |
| `GET` | `/api/v1/governance/risk-categories` |  |
| `GET` | `/api/v1/health/quick` | Quick health check — returns a simple OK/ERROR status. |
| `GET` | `/api/v1/health/system` | Full system health check — returns status of all components. |
| `GET` | `/api/v1/heartbeat/budget/notifications` | 获取预算通知 |
| `GET` | `/api/v1/heartbeat/budget/{agent_id}` | 检查代理预算状态 |
| `POST` | `/api/v1/heartbeat/recovery/orphaned` | 手动触发孤立任务恢复 |
| `GET` | `/api/v1/heartbeat/stats` | 获取调度器统计信息 |
| `GET` | `/api/v1/heartbeat/tasks` | 列出所有调度任务 |
| `POST` | `/api/v1/heartbeat/tasks` | 创建调度任务 |
| `DELETE` | `/api/v1/heartbeat/tasks/{task_id}` | 删除任务 |
| `GET` | `/api/v1/heartbeat/tasks/{task_id}` | 获取调度任务详情 |
| `GET` | `/api/v1/heartbeat/tasks/{task_id}/history` | 获取任务执行历史 |
| `POST` | `/api/v1/heartbeat/tasks/{task_id}/pause` | 暂停任务 |
| `POST` | `/api/v1/heartbeat/tasks/{task_id}/resume` | 恢复任务 |
| `POST` | `/api/v1/heartbeat/tasks/{task_id}/trigger` | 立即触发任务执行 |
| `GET` | `/api/v1/governance/sovereignty` | 读取数据主权/自主等级设置（W9.1）。 |
| `PUT` | `/api/v1/governance/sovereignty` | 更新主权/自主等级设置（影响 AI 介入强度）。 |
| `POST` | `/api/v1/governance/sovereignty/reset` | 恢复默认主权/自主等级设置。 |
| `GET` | `/api/v1/image_to_3d/precision_info` | 查询当前精度档位信息（不创建任务）。 |
| `GET` | `/api/v1/image_to_3d/tasks` | 列出最近任务 |
| `POST` | `/api/v1/image_to_3d/tasks` | 上传多张照片创建重建任务 |
| `DELETE` | `/api/v1/image_to_3d/tasks/{task_id}` | 删除任务（清理 workspace） |
| `GET` | `/api/v1/image_to_3d/tasks/{task_id}` | 查询任务状态 |
| `GET` | `/api/v1/image_to_3d/tasks/{task_id}/result` | 下载最终 mesh 文件 |
| `POST` | `/api/v1/image_to_3d/tasks/{task_id}/run` | 异步触发重建执行 |
| `GET` | `/api/v1/image_to_3d/tasks/{task_id}/sparse` | 下载稀疏点云（COLMAP 输出） |
| `GET` | `/api/v1/jobs` |  |
| `POST` | `/api/v1/jobs` | 创建通用任务（真实落库到任务管理器，返回 job_id 供轮询/SSE 跟踪）。 |
| `GET` | `/api/v1/jobs/stats` |  |
| `DELETE` | `/api/v1/jobs/{job_id}` |  |
| `GET` | `/api/v1/jobs/{job_id}` |  |
| `POST` | `/api/v1/jobs/{job_id}/cancel` |  |
| `GET` | `/api/v1/jobs/{job_id}/progress` |  |
| `GET` | `/api/v1/jobs/{job_id}/stream` |  |
| `GET` | `/api/v1/llm-providers` | 列出所有 Provider 配置 |
| `POST` | `/api/v1/llm-providers` | 新增 Provider 配置 |
| `GET` | `/api/v1/llm-providers/active` | 获取当前激活的 Provider |
| `GET` | `/api/v1/llm-providers/capabilities` | 列出所有支持的能力标签 |
| `POST` | `/api/v1/llm-providers/detect/import` | 执行自动探测并导入可用 Provider |
| `GET` | `/api/v1/llm-providers/detect/preview` | 预览自动探测结果（不写入数据库） |
| `GET` | `/api/v1/llm-providers/router/status` | 获取 Provider 路由器状态 |
| `GET` | `/api/v1/llm-providers/router/strategies` | 列出所有支持的路由策略 |
| `GET` | `/api/v1/llm-providers/status` | Provider 注册表状态摘要 |
| `GET` | `/api/v1/llm-providers/types` | 列出所有支持的 Provider 类型 |
| `DELETE` | `/api/v1/llm-providers/{provider_id}` | 删除 Provider 配置 |
| `GET` | `/api/v1/llm-providers/{provider_id}` | 获取指定 Provider 配置 |
| `PUT` | `/api/v1/llm-providers/{provider_id}` | 更新 Provider 配置 |
| `POST` | `/api/v1/llm-providers/{provider_id}/activate` | 激活指定 Provider（互斥） |
| `POST` | `/api/v1/llm-providers/{provider_id}/enable` | 启用/禁用 Provider |
| `GET` | `/api/v1/llm-providers/{provider_id}/health` | 健康检查指定 Provider |
| `GET` | `/api/v1/llm-providers/{provider_id}/models` | 列出指定 Provider 可用的模型 |
| `POST` | `/api/v1/llm-providers/{provider_id}/test` | 测试 Provider 调用（发送一条对话） |
| `POST` | `/api/v1/llm/chat/stream` | LLM 流式对话补全（SSE）。原生流（Ollama NDJSON/OpenAI SSE）逐 token 输出，其余后端伪流式降级；6xxx 分级错误以错误帧返回。 |
| `POST` | `/api/v1/lnn/batch-inference` | 异步启动批量推理,立即返回 job_id。 |
| `DELETE` | `/api/v1/lnn/cache/clear` | 清空所有模型缓存 |
| `GET` | `/api/v1/lnn/cache/stats` | 获取模型缓存统计信息 |
| `POST` | `/api/v1/lnn/device/clear-cache` | 清空GPU缓存 |
| `GET` | `/api/v1/lnn/device/info` | 返回系统中可用的计算设备信息 |
| `GET` | `/api/v1/lnn/device/status` | 返回当前设备利用率和温度等信息 |
| `GET` | `/api/v1/lnn/health` | LNN 系统健康检查(包含持久层状态) |
| `GET` | `/api/v1/lnn/models` |  |
| `GET` | `/api/v1/lnn/models/{model_name}/info` |  |
| `POST` | `/api/v1/lnn/models/{model_name}/quantize` | 异步启动 INT8 量化任务,立即返回 job_id。 |
| `GET` | `/api/v1/lnn/models/{model_name}/size` | 获取模型及其量化版本的大小信息。 |
| `POST` | `/api/v1/lnn/models/{model_name}/validate` |  |
| `GET` | `/api/v1/lnn/performance` |  |
| `POST` | `/api/v1/lnn/predict` |  |
| `POST` | `/api/v1/lnn/predict-uncertain` | 基于 Bayesian LNN（MC Dropout）的预测 + 不确定性量化。 |
| `POST` | `/api/v1/lnn/predict_stream` | 流式长时序推理（NDJSON 流式响应）。 |
| `POST` | `/api/v1/lnn/predict_windowed` | 窗口化超长序列推理（一次性 JSON 响应）。 |
| `POST` | `/api/v1/lnn/quantize/{task_id}/cancel` | 取消进行中的量化任务。 |
| `GET` | `/api/v1/lnn/quantize/{task_id}/status` | 查询异步量化任务的状态与结果。 |
| `GET` | `/api/v1/lnn/tasks` | 列出所有训练任务 |
| `POST` | `/api/v1/lnn/train` | 异步启动 LNN 训练,立即返回 job_id。 |
| `POST` | `/api/v1/lnn/train/dry_run` |  |
| `POST` | `/api/v1/lnn/train/{task_id}/cancel` | 取消正在运行的训练任务。 |
| `GET` | `/api/v1/lnn/train/{task_id}/stream` | SSE 端点,用于实时训练状态更新。 |
| `GET` | `/api/v1/logs/stats` |  |
| `GET` | `/api/v1/logs/{buffer_type}` |  |
| `GET` | `/api/v1/materials/` | 获取物料列表，支持分类、状态筛选、关键词搜索和分页。 |
| `POST` | `/api/v1/materials/` | 创建新物料。 |
| `POST` | `/api/v1/materials/seed` | 初始化种子数据（仅在物料表为空时插入）。 |
| `GET` | `/api/v1/materials/stats/summary` | 获取物料统计汇总：总数、低库存数、缺货数。 |
| `DELETE` | `/api/v1/materials/{material_id}` | 删除物料。 |
| `GET` | `/api/v1/materials/{material_id}` | 根据 ID 获取单个物料详情。 |
| `PUT` | `/api/v1/materials/{material_id}` | 更新物料信息。 |
| `POST` | `/api/v1/materials/{material_id}/purchase` | 物料采购：更新供应商并增加库存数量，自动重算状态。 |
| `POST` | `/api/v1/materials/{material_id}/stock-in` | 物料入库：增加库存数量并自动重算状态。 |
| `POST` | `/api/v1/nl2cad/extract-params` | 从自然语言描述中提取CAD参数（不生成模型）。 |
| `POST` | `/api/v1/nl2cad/full-pipeline` | 执行完整的NL-to-NC流程。 |
| `POST` | `/api/v1/nl2cad/generate` | 从自然语言描述生成3D模型。 |
| `POST` | `/api/v1/nl2cad/generate-nc` | 根据工艺规划生成NC代码。 |
| `POST` | `/api/v1/nl2cad/process-planning` | 根据CAD参数生成工艺规划。 |
| `POST` | `/api/v1/nl2cad/refine` | 根据用户指令微调3D模型。 |
| `POST` | `/api/v1/nl2cad/regenerate-params` | 按参数覆写重新执行 CadQuery 脚本生成模型（不调用 LLM，支撑前端滑杆调参）。 |
| `GET` | `/api/v1/notifications` | 聚合顶栏通知：进行中/失败任务 + 未处理告警 + 低库存物料 + 待处理质量异常。 |
| `GET` | `/api/v1/parametric_geometry/precision_info` | 查询当前精度档位信息与工业硬门槛（不创建任务）。 |
| `GET` | `/api/v1/parametric_geometry/tasks` | 列出最近任务 |
| `POST` | `/api/v1/parametric_geometry/tasks` | 创建参数化几何任务（输入阶段 2 confirmed_features.json 路径） |
| `DELETE` | `/api/v1/parametric_geometry/tasks/{task_id}` | 取消/删除任务 |
| `GET` | `/api/v1/parametric_geometry/tasks/{task_id}` | 查询任务状态 |
| `POST` | `/api/v1/parametric_geometry/tasks/{task_id}/finalize` | 基于审核结果重新生成最终 STEP（第二轮 STEP 生成） |
| `GET` | `/api/v1/parametric_geometry/tasks/{task_id}/result` | 获取 STEP 生成结果 + 装配摘要 + 特征列表 |
| `POST` | `/api/v1/parametric_geometry/tasks/{task_id}/review` | 工程师审核单个特征在 STEP 中的表达（第一轮审核） |
| `POST` | `/api/v1/parametric_geometry/tasks/{task_id}/run` | 异步触发参数化几何流水线执行 |
| `GET` | `/api/v1/parametric_geometry/tasks/{task_id}/step/download` | 下载 STEP 文件 |
| `GET` | `/api/v1/plugins` |  |
| `GET` | `/api/v1/plugins/health` |  |
| `GET` | `/api/v1/plugins/marketplace` | 获取插件市场列表。 |
| `POST` | `/api/v1/plugins/marketplace/{plugin_id}/install` | 安装市场插件：对已注册插件执行真实启用；内置源码包提示直接启用。 |
| `GET` | `/api/v1/plugins/workers` |  |
| `POST` | `/api/v1/plugins/workers/{plugin_id}/start` |  |
| `POST` | `/api/v1/plugins/workers/{plugin_id}/stop` |  |
| `DELETE` | `/api/v1/plugins/{plugin_id}` |  |
| `GET` | `/api/v1/plugins/{plugin_id}` |  |
| `GET` | `/api/v1/plugins/{plugin_id}/capabilities` |  |
| `PUT` | `/api/v1/plugins/{plugin_id}/capabilities/{capability}` |  |
| `PUT` | `/api/v1/plugins/{plugin_id}/config` |  |
| `GET` | `/api/v1/plugins/{plugin_id}/dependencies` |  |
| `POST` | `/api/v1/plugins/{plugin_id}/disable` |  |
| `POST` | `/api/v1/plugins/{plugin_id}/enable` |  |
| `GET` | `/api/v1/plugins/{plugin_id}/logs` |  |
| `POST` | `/api/v1/plugins/{plugin_id}/reload` |  |
| `GET` | `/api/v1/postprocessor/dialects` | 列出所有方言（内置 + 声明镜像）。 |
| `POST` | `/api/v1/postprocessor/dialects` | 新建声明式方言：创建目录 + dialect.yaml + 骨架模板。 |
| `POST` | `/api/v1/postprocessor/dialects/preview` | NC 输出预览：给定样例刀路输入，渲染方言完整 NC 输出。 |
| `POST` | `/api/v1/postprocessor/dialects/template` | 读取方言模板文件内容（工艺员编辑模板用）。 |
| `DELETE` | `/api/v1/postprocessor/dialects/{dialect_id}` | 删除声明式方言（仅限 postprocessor-plugins/ 下的非内置方言）。 |
| `GET` | `/api/v1/postprocessor/dialects/{dialect_id}` | 方言详情：声明 + 模板方法列表 + 编译状态。 |
| `GET` | `/api/v1/postprocessor/dialects/{dialect_id}/params` | 读取方言参数：有效配置（继承链合并）+ 方言自己的参数。 |
| `PUT` | `/api/v1/postprocessor/dialects/{dialect_id}/params` | 保存方言参数：写回 dialect.yaml 的 params 段。 |
| `PUT` | `/api/v1/postprocessor/dialects/{dialect_id}/template` | 保存方言模板内容（写回模板文件）。 |
| `POST` | `/api/v1/process-explainer/chat` | 多轮对话：基于会话历史的上下文追问。 |
| `POST` | `/api/v1/process-explainer/cleanup` | 清理过期会话（默认 7 天前的会话）。 |
| `POST` | `/api/v1/process-explainer/explain-nc` | 解释 NC / G 代码（结合 ToolpathParser 结构化解析）。 |
| `POST` | `/api/v1/process-explainer/explain-process` | 将工艺规划（特征→工艺→刀具→参数）转为自然语言解释。 |
| `POST` | `/api/v1/process-explainer/sessions` | 创建新的对话会话，返回 session_id。 |
| `DELETE` | `/api/v1/process-explainer/sessions/{session_id}` | 清空指定会话的所有消息。 |
| `GET` | `/api/v1/process-explainer/sessions/{session_id}` | 获取指定会话的历史消息。 |
| `GET` | `/api/v1/process-routes/` | 获取工艺路线列表，支持按状态、零件类型筛选。 |
| `POST` | `/api/v1/process-routes/` | 创建工艺路线（含工序步骤）。 |
| `POST` | `/api/v1/process-routes/seed` | 填充工艺路线演示数据：6条路线及其工序。 |
| `DELETE` | `/api/v1/process-routes/{route_id}` | 删除工艺路线及其所有工序。 |
| `GET` | `/api/v1/process-routes/{route_id}` | 获取工艺路线详情（含所有工序步骤）。 |
| `PUT` | `/api/v1/process-routes/{route_id}` | 更新工艺路线（含工序步骤替换）。 |
| `GET` | `/api/v1/production/dashboard/` | 仪表盘 KPI：今日产量、良品率、OEE、活跃告警数。 |
| `GET` | `/api/v1/production/lines/` | 获取产线列表及各班次数据。 |
| `GET` | `/api/v1/production/records/` | 获取生产记录列表，支持按日期范围、产线、班次筛选。 |
| `POST` | `/api/v1/production/seed` | 填充生产演示数据：14天 x 5产线 x 3班次 + 8个工单。 |
| `GET` | `/api/v1/production/stats` | 按天聚合生产统计（近 N 天计划/实际产量、良品率、设备利用率、达成率）。 |
| `GET` | `/api/v1/production/stats/summary` | 月度汇总 KPI。 |
| `GET` | `/api/v1/production/work-orders/` | 获取工单列表，支持按状态、优先级筛选。 |
| `GET` | `/api/v1/production/work-orders/{wo_id}` | 获取单个工单详情。 |
| `PUT` | `/api/v1/production/work-orders/{wo_id}` | 更新工单信息。 |
| `POST` | `/api/v1/project-packages/export` | 导出项目为 ``.lomo`` 包. |
| `GET` | `/api/v1/project-packages/exports` | 分页列出导出记录（支持 project_id / status / exported_by 过滤）. |
| `DELETE` | `/api/v1/project-packages/exports/{export_id}` | 删除导出包文件 + 记录. |
| `GET` | `/api/v1/project-packages/exports/{export_id}` | 查询导出记录详情，或下载 ``.lomo`` 文件. |
| `POST` | `/api/v1/project-packages/import` | 导入 ``.lomo`` 包到目标项目. |
| `GET` | `/api/v1/project-packages/imports` | 分页列出导入记录（支持 target_project_id / status / imported_by 过滤）. |
| `POST` | `/api/v1/project-packages/preview` | 预览 ``.lomo`` 包内容（返回 manifest，不实际导入）. |
| `POST` | `/api/v1/project-packages/validate` | 校验 ``.lomo`` 包完整性（不实际导入）. |
| `POST` | `/api/v1/project-sync/clone` | 克隆远端项目（git clone + 注册到 DB）. |
| `GET` | `/api/v1/project-sync/projects` | 分页列出项目（支持状态/作者过滤）. |
| `POST` | `/api/v1/project-sync/projects` | 创建项目（执行 git init + 写入 .lomo-project.yaml + 可选首 commit）. |
| `DELETE` | `/api/v1/project-sync/projects/{project_id}` | 删除项目. |
| `GET` | `/api/v1/project-sync/projects/{project_id}` | 获取项目详情（含当前状态 + 可选资源引用 / 同步记录）. |
| `POST` | `/api/v1/project-sync/projects/{project_id}/commit` | 提交变更（重新计算资源 hash → 更新清单 → git add → git commit）. |
| `POST` | `/api/v1/project-sync/projects/{project_id}/pull` | 拉取远端更新（git pull origin <branch>）. |
| `POST` | `/api/v1/project-sync/projects/{project_id}/push` | 推送到远端仓库（git push origin <branch>）. |
| `GET` | `/api/v1/project-sync/projects/{project_id}/records` | 查询项目的同步记录（按时间倒序）. |
| `DELETE` | `/api/v1/project-sync/projects/{project_id}/resources` | 删除项目的资源引用（按 resource_uri 精确匹配）. |
| `GET` | `/api/v1/project-sync/projects/{project_id}/resources` | 列出项目的资源引用（可选按类型过滤）. |
| `POST` | `/api/v1/project-sync/projects/{project_id}/resources` | 添加资源引用到项目. |
| `GET` | `/api/v1/project-sync/projects/{project_id}/status` | 查询项目的 Git 状态（执行 git status 推导状态机）. |
| `GET` | `/api/v1/quality/` | 获取质量检验记录列表，支持按类型、结果、日期范围筛选。 |
| `POST` | `/api/v1/quality/` | 创建质量检验记录。 |
| `GET` | `/api/v1/quality/anomalies/` | 获取质量异常列表。 |
| `POST` | `/api/v1/quality/seed` | 填充质量检验演示数据。 |
| `GET` | `/api/v1/quality/stats/` | 获取质量统计：今日检验数、合格率、异常数、异常类型分布。 |
| `GET` | `/api/v1/quality/{record_id}` | 获取质量检验记录详情。 |
| `GET` | `/api/v1/resource-cards/datasets/{dataset_id}` | 获取数据集卡片（聚合元数据 + 最新版本指标 + README + lineage 摘要）. |
| `GET` | `/api/v1/resource-cards/datasets/{dataset_id}/lineage` | 获取数据集的 lineage 摘要（按层分组 + 关键路径）. |
| `GET` | `/api/v1/resource-cards/datasets/{dataset_id}/metrics` | 获取数据集指标（版本数 / 总行数 / 总大小 / 各版本明细）. |
| `PUT` | `/api/v1/resource-cards/datasets/{dataset_id}/readme` | 更新数据集 README（upsert 语义：不存在则创建，存在则覆盖）. |
| `GET` | `/api/v1/resource-cards/models` | 分页列出模型产物（支持 owner/type/status/tag 过滤）. |
| `POST` | `/api/v1/resource-cards/models` | 注册新模型产物. |
| `DELETE` | `/api/v1/resource-cards/models/{model_id}` | 删除模型卡片（同时删除关联的指标历史）. |
| `GET` | `/api/v1/resource-cards/models/{model_id}` | 获取模型卡片详情（聚合 ModelArtifact + Snapshot 数 + lineage 摘要）. |
| `PUT` | `/api/v1/resource-cards/models/{model_id}` | 更新模型卡片字段（部分更新，仅非 None 字段被写入）. |
| `GET` | `/api/v1/resource-cards/models/{model_id}/lineage` | 获取模型的 lineage 摘要（按层分组 + 关键路径）. |
| `GET` | `/api/v1/resource-cards/models/{model_id}/metrics` | 获取模型指标历史（追加式记录列表）. |
| `POST` | `/api/v1/resource-cards/models/{model_id}/metrics` | 追加一条指标记录到模型历史（同时更新当前指标快照）. |
| `POST` | `/api/v1/rl-agent/act` | 执行 RL 决策（不走工作流，直接调用服务层）. |
| `POST` | `/api/v1/rl-agent/training/start` | 启动离线 RL 训练（PPO + 真实轨迹回放；数据缺失时拒绝）. |
| `GET` | `/api/v1/rl-agent/training/status` | 查询当前 RL 训练状态. |
| `POST` | `/api/v1/rl-agent/training/stop` | 停止当前 RL 训练. |
| `GET` | `/api/v1/rl-agent/versions` | 分页列出 RL 策略版本. |
| `GET` | `/api/v1/rl-agent/versions/{version}` | 查询 RL 策略版本详情. |
| `GET` | `/api/v1/sharp/ablation` | 查询当前消融模式与可选模式列表。 |
| `POST` | `/api/v1/sharp/ablation` | 切换消融模式（需 sharp:write 权限）。 |
| `GET` | `/api/v1/sharp/status` | 获取 SHARP 服务状态。 |
| `DELETE` | `/api/v1/sharp/trajectory` | 清空轨迹库（需 sharp:write 权限）。 |
| `POST` | `/api/v1/sharp/trajectory/query` | 查询历史轨迹列表（带过滤）。 |
| `GET` | `/api/v1/sharp/trajectory/{verification_id}` | 按 verification_id 取单条历史轨迹。 |
| `POST` | `/api/v1/sharp/verify` | 验证单个三元组。 |
| `POST` | `/api/v1/sharp/verify/batch` | 批量验证三元组（单次最多 50 条）。 |
| `POST` | `/api/v1/signal-fusion-kb/correlate/chatter` | 将信号样本关联为 ChatterPredictor 可消费的特征。 |
| `POST` | `/api/v1/signal-fusion-kb/correlate/wear` | 将信号样本关联为 ToolWearPredictor 可消费的 sensor_features。 |
| `POST` | `/api/v1/signal-fusion-kb/fuse` | 将多个信号样本融合为统一特征向量。 |
| `GET` | `/api/v1/signal-fusion-kb/health` | 健康检查。 |
| `POST` | `/api/v1/signal-fusion-kb/retrieve` | 检索与给定特征向量相似的信号样本。 |
| `GET` | `/api/v1/signal-fusion-kb/samples` | 列出所有信号融合样本（分页）。 |
| `POST` | `/api/v1/signal-fusion-kb/samples` | 注册单个信号样本到知识库。 |
| `POST` | `/api/v1/signal-fusion-kb/samples/batch` | 批量注册信号样本。 |
| `DELETE` | `/api/v1/signal-fusion-kb/samples/by-type/{signal_type}` | 按信号类型批量删除。 |
| `GET` | `/api/v1/signal-fusion-kb/samples/by-type/{signal_type}` | 按信号类型列出样本。 |
| `DELETE` | `/api/v1/signal-fusion-kb/samples/{sample_id}` | 按样本 ID 删除。 |
| `GET` | `/api/v1/signal-fusion-kb/stats` | 返回多源信号融合知识库统计信息。 |
| `GET` | `/api/v1/skills` |  |
| `POST` | `/api/v1/skills/create` |  |
| `POST` | `/api/v1/skills/export` |  |
| `POST` | `/api/v1/skills/import` |  |
| `POST` | `/api/v1/skills/inject` |  |
| `POST` | `/api/v1/skills/marketplace/download` |  |
| `GET` | `/api/v1/skills/marketplace/list` |  |
| `POST` | `/api/v1/skills/marketplace/publish` |  |
| `POST` | `/api/v1/skills/marketplace/rate` |  |
| `GET` | `/api/v1/skills/marketplace/search` |  |
| `DELETE` | `/api/v1/skills/marketplace/{skill_id}` |  |
| `POST` | `/api/v1/skills/rate` |  |
| `POST` | `/api/v1/skills/reload` |  |
| `GET` | `/api/v1/skills/stats` |  |
| `DELETE` | `/api/v1/skills/{skill_id}` |  |
| `GET` | `/api/v1/skills/{skill_id}` |  |
| `PUT` | `/api/v1/skills/{skill_id}` |  |
| `GET` | `/api/v1/skills/{skill_id}/versions` |  |
| `GET` | `/api/v1/snapshots` | 列出实验快照（按 created_at 倒序）。 |
| `POST` | `/api/v1/snapshots` | 创建实验快照（自动采集 git_sha 与环境信息）。 |
| `GET` | `/api/v1/snapshots/{snapshot_id}` | 获取快照详情（含完整 config / metrics / environment）. |
| `POST` | `/api/v1/snapshots/{snapshot_id}/reproduce` | 根据快照一键复现：重建 WorkflowSpec 并启动新工作流运行。 |
| `POST` | `/api/v1/system/backup` | 创建一次桌面 SQLite 数据全量备份。 |
| `POST` | `/api/v1/system/backup/{backup_id}/restore` | 将指定备份恢复到目标目录（不覆盖同名文件）。 |
| `GET` | `/api/v1/system/backups` | 列出历史备份（按时间倒序）。 |
| `GET` | `/api/v1/system/status` | 系统状态：版本、运行时长、核心组件健康（真实查询）。 |
| `GET` | `/api/v1/system/update-check` | 检查 GitHub Releases 最新版本 |
| `GET` | `/api/v1/system/version` |  |
| `GET` | `/api/v1/task-checkout/agents/{agent_id}/status` |  |
| `GET` | `/api/v1/task-checkout/board` |  |
| `POST` | `/api/v1/task-checkout/checkout` |  |
| `POST` | `/api/v1/task-checkout/cleanup` |  |
| `GET` | `/api/v1/task-checkout/locks` |  |
| `DELETE` | `/api/v1/task-checkout/locks/{task_id}` | 强制释放任务执行锁。 |
| `GET` | `/api/v1/task-checkout/queue` |  |
| `POST` | `/api/v1/task-checkout/queue/enqueue` |  |
| `POST` | `/api/v1/task-checkout/queue/process` |  |
| `POST` | `/api/v1/task-checkout/tasks` |  |
| `POST` | `/api/v1/task-checkout/tasks/{task_id}/abandon` |  |
| `POST` | `/api/v1/task-checkout/tasks/{task_id}/complete` |  |
| `POST` | `/api/v1/task-checkout/tasks/{task_id}/fail` |  |
| `POST` | `/api/v1/task-checkout/tasks/{task_id}/heartbeat` |  |
| `GET` | `/api/v1/task-checkout/tasks/{task_id}/history` |  |
| `POST` | `/api/v1/template_market/export/{branch_id}` | Export a template with optional evolution history. |
| `POST` | `/api/v1/template_market/import` | Import a template with optional parameter adaptation. |
| `POST` | `/api/v1/template_market/publish` | Publish a validated template to the marketplace. |
| `POST` | `/api/v1/template_market/subscribe` | Subscribe to template category updates. |
| `GET` | `/api/v1/template_market/subscriptions/{project_id}` | Get subscriptions for a project. |
| `GET` | `/api/v1/template_market/sync/{branch_id}` | Get incremental changes for a branch (delta sync). |
| `GET` | `/api/v1/template_market/templates/{branch_id}/metrics` | Get effectiveness metrics for a template. |
| `GET` | `/api/v1/template_market/trending` | Get trending templates based on adoption rate. |
| `GET` | `/api/v1/templates/ab_tests` | List experiments. |
| `POST` | `/api/v1/templates/ab_tests` | Create a new A/B experiment. |
| `POST` | `/api/v1/templates/ab_tests/assign` | Assign a project to a branch in all active experiments. |
| `POST` | `/api/v1/templates/ab_tests/record` | Record an execution in an experiment. |
| `GET` | `/api/v1/templates/ab_tests/{experiment_id}` | Get experiment details. |
| `POST` | `/api/v1/templates/ab_tests/{experiment_id}/conclude` | Auto-conclude an experiment (merge or rollback). |
| `POST` | `/api/v1/templates/ab_tests/{experiment_id}/evaluate` | Evaluate an experiment. |
| `GET` | `/api/v1/templates/branches/` |  |
| `POST` | `/api/v1/templates/branches/` |  |
| `POST` | `/api/v1/templates/branches/merge` |  |
| `DELETE` | `/api/v1/templates/branches/{branch_id}` |  |
| `GET` | `/api/v1/templates/branches/{branch_id}` |  |
| `PUT` | `/api/v1/templates/branches/{branch_id}` |  |
| `GET` | `/api/v1/templates/branches/{branch_id}/log` |  |
| `GET` | `/api/v1/templates/evolution/history` | Get evolution history. |
| `POST` | `/api/v1/templates/evolution/metrics` | Update metrics for trigger evaluation. |
| `GET` | `/api/v1/templates/evolution/suggestions` | List evolution suggestions. |
| `POST` | `/api/v1/templates/evolution/suggestions` | Create a new evolution suggestion. |
| `POST` | `/api/v1/templates/evolution/suggestions/apply` | Apply an evolution suggestion to a branch. |
| `POST` | `/api/v1/templates/evolution/triggers/evaluate` | Evaluate all evolution triggers. |
| `GET` | `/api/v1/templates/patterns` | List all discovered patterns. |
| `POST` | `/api/v1/templates/patterns/analyze` | Run pattern analysis on accumulated execution data. |
| `GET` | `/api/v1/templates/patterns/anti_patterns` | List all detected anti-patterns. |
| `POST` | `/api/v1/templates/patterns/record` | Record a task execution for pattern analysis. |
| `GET` | `/api/v1/templates/patterns/{pattern_id}` | Get details of a specific pattern. |
| `GET` | `/api/v1/templates/patterns/{pattern_id}/suggestions` | Get auto-generated suggestions from a pattern. |
| `POST` | `/api/v1/templates/updates/apply/{notification_id}` | Apply an update notification. |
| `POST` | `/api/v1/templates/updates/dismiss/{notification_id}` | Dismiss an update notification. |
| `GET` | `/api/v1/templates/updates/preview/{notification_id}` | Preview an update notification. |
| `POST` | `/api/v1/templates/updates/scan` | Scan for applicable updates for a project. |
| `GET` | `/api/v1/templates/updates/{project_id}` | Get update notifications for a project. |
| `GET` | `/api/v1/tools/` | 获取刀具列表，支持类型、状态筛选和关键词搜索。 |
| `POST` | `/api/v1/tools/` | 创建新刀具。 |
| `POST` | `/api/v1/tools/seed` | 初始化种子数据（仅在刀具表为空时插入）。 |
| `GET` | `/api/v1/tools/stats/summary` | 获取刀具统计汇总：总数、磨损数、报废数、平均寿命剩余。 |
| `DELETE` | `/api/v1/tools/{tool_id}` | 删除刀具。 |
| `GET` | `/api/v1/tools/{tool_id}` | 根据 ID 获取单个刀具详情。 |
| `PUT` | `/api/v1/tools/{tool_id}` | 更新刀具信息。 |
| `GET` | `/api/v1/tools/{tool_id}/life-prediction` | 获取刀具寿命预测信息。 |
| `POST` | `/api/v1/tools/{tool_id}/wear` | 更新刀具磨损信息。 |
| `DELETE` | `/api/v1/user-sovereignty/audit-log/clear` |  |
| `POST` | `/api/v1/user-sovereignty/audit-log/export` |  |
| `POST` | `/api/v1/user-sovereignty/audit-log/query` |  |
| `POST` | `/api/v1/user-sovereignty/audit-log/record` |  |
| `POST` | `/api/v1/user-sovereignty/audit-log/search` |  |
| `GET` | `/api/v1/user-sovereignty/audit-log/statistics` |  |
| `POST` | `/api/v1/user-sovereignty/predict` |  |
| `GET` | `/api/v1/user-sovereignty/settings` |  |
| `GET` | `/api/v1/users` |  |
| `GET` | `/api/v1/users/me/permissions` |  |
| `PUT` | `/api/v1/users/{username}/role` |  |
| `PUT` | `/api/v1/users/{username}/status` |  |
| `GET` | `/api/v1/version` | 兼容端点：GET /api/v1/version（旧版顶层版本接口）。 |
| `POST` | `/api/v1/wear/calibrate` |  |
| `POST` | `/api/v1/wear/calibrate-realtime` |  |
| `POST` | `/api/v1/wear/compensation` |  |
| `GET` | `/api/v1/wear/cross-dataset-analysis` |  |
| `GET` | `/api/v1/wear/models` |  |
| `POST` | `/api/v1/wear/predict` |  |
| `POST` | `/api/v1/wear/predict-from-signals` |  |
| `POST` | `/api/v1/wear/remaining-life` |  |
| `POST` | `/api/v1/wear/suggest` |  |
| `POST` | `/api/v1/wear/threshold` |  |
| `POST` | `/api/v1/wear/train-uniwear` |  |
| `GET` | `/api/v1/wear/uniwear-materials` |  |
| `GET` | `/api/v1/workflow-templates` | 分页列出模板（支持分类/标签/作者过滤，多种排序）. |
| `POST` | `/api/v1/workflow-templates/publish` | 发布工作流模板（新模板或新版本）. |
| `GET` | `/api/v1/workflow-templates/search` | 关键词搜索模板（name / description / tags / author 模糊匹配）. |
| `GET` | `/api/v1/workflow-templates/stats` | 市场全局统计（模板总数 / 总下载 / 平均评分）. |
| `GET` | `/api/v1/workflow-templates/{template_id}` | 获取模板详情（含指定版本的 manifest + spec）. |
| `GET` | `/api/v1/workflow-templates/{template_id}/download` | 下载模板（自增下载计数，返回完整 manifest + spec）. |
| `POST` | `/api/v1/workflow-templates/{template_id}/rate` | 给模板评分（1.0-5.0），增量更新 avg_rating / rating_count. |
| `POST` | `/api/v1/workflow-templates/{template_id}/unpublish` | 下架模板（status -> unpublished，不删除数据）. |
| `GET` | `/api/v1/workflow-templates/{template_id}/versions` | 列出某模板的所有版本（按创建时间倒序）. |
| `POST` | `/api/v1/world-model/preview` | 世界模型预演卡：给定工艺/参数上下文预测切削力、颤振倾向、碰撞概率（确认页事前预演）。 |
| `GET` | `/api/v1/workflows` | 列出工作流运行记录。 |
| `POST` | `/api/v1/workflows/run` | 提交工作流，返回 workflow_run_id。 |
| `POST` | `/api/v1/workflows/validate` | 仅校验 WorkflowSpec，不执行。返回校验错误列表（空表示通过）。 |
| `DELETE` | `/api/v1/workflows/{workflow_run_id}` | 删除工作流运行记录（含节点状态）。 |
| `GET` | `/api/v1/workflows/{workflow_run_id}` | 获取工作流运行状态（含各节点状态）。 |
| `POST` | `/api/v1/workflows/{workflow_run_id}/cancel` | 取消工作流。下游未启动节点标记为 SKIPPED。 |
| `POST` | `/api/v1/workflows/{workflow_run_id}/resume` | 断点续跑：从指定 workflow_run_id 继续，仅重跑 FAILED/PENDING 节点。 |
| `GET` | `/api/v1/workflows/{workflow_run_id}/stream` | SSE 事件流：实时推送节点状态变化。 |
| `POST` | `/api/v1/world-model/predict` | 执行世界模型轨迹预测（不走工作流，直接调用服务层）. |
| `GET` | `/api/v1/world-model/versions` | 分页列出世界模型版本. |
| `GET` | `/api/v1/world-model/versions/{version}` | 查询世界模型版本详情. |

### /audit-log

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/audit-log` | 审计日志查询（C类，仅管理员） |

### /dxf/batch

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/dxf/batch` | 批量处理多个 DXF（最多 20 个）。 |

### /dxf/batch-async

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/dxf/batch-async` | 批量处理多个 DXF（异步任务模式），立即返回 job_id。 |

### /dxf/batch-status/{job_id}

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/dxf/batch-status/{job_id}` | 查询批量处理任务进度（done/success/failed/results）。 |

### /dxf/e2e-fixture

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/dxf/e2e-fixture` | 用 data/test_fixtures/ 下的所有 DXF 跑端到端测试。 |

### /dxf/process

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/dxf/process` | 处理单个 DXF 文件（端到端）。 |

### /execute

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/execute` | 工艺参数下发（T类，paper_only默认） |

### /experience

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/experience` | 分页查询切削实测记录。 |

### /experience/batch

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/experience/batch` | 批量采集（MTConnect 管道 / CSV 导入）。 |

### /experience/capture

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/experience/capture` | 单条切削实测采集。 |

### /experience/stats

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/experience/stats` | 聚合统计（节拍/粗糙度/磨损均值、合格率、异常率）。 |

### /experience/{record_id}

| 方法 | 路径 | 说明 |
|------|------|------|
| `DELETE` | `/experience/{record_id}` | 删除记录（管理用途）。 |
| `GET` | `/experience/{record_id}` | 单条详情。 |

### /explain

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/explain` | 模型预测结果解释 |

### /health

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查（R类） |
| `GET` | `/health` | 健康检查 |

### /knowledge-graph/edges

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/knowledge-graph/edges` | 列出/过滤关系。 |

### /knowledge-graph/materials-for-tool

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/knowledge-graph/materials-for-tool` | 某刀具能加工的所有材料。 |

### /knowledge-graph/neighbors

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/knowledge-graph/neighbors/{node_id}` | N 跳邻居。 |

### /knowledge-graph/nodes

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/knowledge-graph/nodes` | 列出/搜索节点。 |
| `GET` | `/knowledge-graph/nodes/{node_id}` | 按 ID 取节点。 |

### /knowledge-graph/process-chain

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/knowledge-graph/process-chain/{feature_id}` | 某 feature 的工艺链。 |

### /knowledge-graph/query

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/knowledge-graph/query` | 统一查询入口。 |

### /knowledge-graph/stats

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/knowledge-graph/stats` | 图规模统计。 |

### /knowledge-graph/tools-for-material

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/knowledge-graph/tools-for-material` | 某材料适配的所有刀具。 |

### /models

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/models` | 已注册模型列表（R类） |

### /models/{name}

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/models/{name}/info` | 模型详细信息（R类） |

### /optimizer/baselines

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/optimizer/baselines` | 列出基线参数库（L0 经验表），支持按材料/加工类型过滤。 |

### /optimizer/compare

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/optimizer/compare` | A/B 两组结果对比。 |

### /optimizer/evaluate

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/optimizer/evaluate` | 评估单条实测结果。 |

### /optimizer/recommend

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/optimizer/recommend` | 推荐切削参数（分层策略 + 物理安全钳制）。 |

### /pipeline/execute

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/pipeline/execute` | 执行工作流管线（B类，需要认证） |

### /pipeline/history

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/pipeline/history` | 查询管线执行历史（R类，需要认证） |

### /pipeline/{pipeline_id}

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/pipeline/{pipeline_id}/trace` | 获取管线执行追踪详情（R类，需要认证） |

### /predict

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/predict` | LNN 预测（R类） |

### /stats

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/stats` | 模块统计信息 |

### /status

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/status` | 总体系统状态。 |

### /status/postprocessors

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/status/postprocessors` | 列出已注册的后处理器。 |

### /status/research-bridge

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/status/research-bridge` | 桥接层详情。 |

### /tokens

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/tokens` | 列出所有 Agent Token |
| `POST` | `/tokens` | 创建 Agent Token |

### /tokens/revoke-t-all

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/tokens/revoke-t-all` | 一键撤销所有 T 类 Token（紧急停止） |

### /tokens/{agent_id}

| 方法 | 路径 | 说明 |
|------|------|------|
| `DELETE` | `/tokens/{agent_id}` | 撤销 Agent Token |

### /train

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/train` | 启动训练（B类，异步，返回job_id） |

### /train/{job_id}

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/train/{job_id}` | 训练状态（R类） |
| `GET` | `/train/{job_id}/stream` | 训练进度SSE流（R类） |


## LNN 模型 API

LNN（Liquid Neural Network）模型管理接口，支持模型预测、训练、量化等功能。

### `POST` `/api/v1/lnn/batch-inference`

**异步启动批量推理,立即返回 job_id。**

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `LNNBatchInferenceRequest` | `-` | 是 |  |
| `idempotency_key` | `str | None` | `Header(...)` | 否 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `DELETE` `/api/v1/lnn/cache/clear`

**清空所有模型缓存**

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/cache/stats`

**获取模型缓存统计信息**

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/device/clear-cache`

**清空GPU缓存**

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/device/info`

**返回系统中可用的计算设备信息**

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/device/status`

**返回当前设备利用率和温度等信息**

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/health`

**LNN 系统健康检查(包含持久层状态)**

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/models`

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/models/{model_name}/info`

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `model_name` | `str` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/models/{model_name}/quantize`

**异步启动 INT8 量化任务,立即返回 job_id。**

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `model_name` | `str` | 是 |  |

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `LNNQuantizeRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/models/{model_name}/size`

**获取模型及其量化版本的大小信息。**

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `model_name` | `str` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/models/{model_name}/validate`

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `model_name` | `str` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/performance`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `model` | `str | None` | `None` | 否 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/predict`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `LNNPredictRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/predict-uncertain`

**基于 Bayesian LNN（MC Dropout）的预测 + 不确定性量化。**

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `LNNPredictRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/predict_stream`

**流式长时序推理（NDJSON 流式响应）。**

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `LNNStreamPredictRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/predict_windowed`

**窗口化超长序列推理（一次性 JSON 响应）。**

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `LNNWindowedPredictRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/quantize/{task_id}/cancel`

**取消进行中的量化任务。**

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `task_id` | `str` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/quantize/{task_id}/status`

**查询异步量化任务的状态与结果。**

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `task_id` | `str` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/tasks`

**列出所有训练任务**

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/train`

**异步启动 LNN 训练,立即返回 job_id。**

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `LNNTrainRequest` | `-` | 是 |  |
| `idempotency_key` | `str | None` | `Header(...)` | 否 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/train/dry_run`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `request` | `LNNTrainDryRunRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/lnn/train/{task_id}/cancel`

**取消正在运行的训练任务。**

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `task_id` | `str` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/lnn/train/{task_id}/stream`

**SSE 端点,用于实时训练状态更新。**

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `task_id` | `str` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误


## 刀具磨损预测 API

刀具磨损预测和工艺优化接口，支持磨损曲线预测、剩余寿命评估等功能。

### `POST` `/api/v1/signal-fusion-kb/correlate/wear`

**将信号样本关联为 ToolWearPredictor 可消费的 sensor_features。**

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `req` | `CorrelateWearRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/tools/{tool_id}/wear`

**更新刀具磨损信息。**

**路径参数：**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `tool_id` | `str` | 是 |  |

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `ToolWearUpdate` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/calibrate`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `CalibrateRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/calibrate-realtime`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `RealTimeCalibrateRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/compensation`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `CompensationRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/wear/cross-dataset-analysis`

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/wear/models`

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/predict`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `WearPredictRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/predict-from-signals`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `features` | `dict[str, float]` | `-` | 是 |  |
| `material` | `str` | `'tc4'` | 否 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/remaining-life`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `RemainingLifeRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/suggest`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `body` | `SuggestRequest` | `-` | 是 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/threshold`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `material_type` | `str` | `'default'` | 否 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `POST` `/api/v1/wear/train-uniwear`

**请求体参数：**

| 参数名 | 类型 | 默认值 | 必填 | 说明 |
|--------|------|--------|------|------|
| `data_dir` | `str` | `'python/data/uniwear'` | 否 |  |
| `model_type` | `str` | `'random_forest'` | 否 |  |

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误

### `GET` `/api/v1/wear/uniwear-materials`

**响应：**

- **200**: 成功响应
- **400**: 请求参数错误
- **404**: 资源未找到
- **500**: 服务器内部错误


## 数据模型

API 请求和响应使用的 Pydantic 模型定义。

### `AgentStateSaveRequest`

保存 Agent 状态的请求体（白名单字段）。

仅允许更新业务字段，agent_id / created_at / updated_at / state_version
等内部字段由服务端管理，不接受客户端传入。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `current_task_id` | `str | None` | 否 | `None` | 当前任务ID | - |
| `status` | `AgentStatus | None` | 否 | `None` | Agent 状态 | - |
| `metadata` | `dict[str, Any]` | 是 | `-` | 元数据 | - |

### `CheckpointSaveRequest`

保存 Checkpoint 的请求体（白名单字段）。

checkpoint_id / created_at / file_size_bytes 由服务端管理，
不接受客户端传入。checkpoint_type 通过枚举校验防止非法值。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `epoch` | `int` | 否 | `0` | 训练轮次 | ≥ 0 |
| `step` | `int` | 否 | `0` | 训练步数 | ≥ 0 |
| `best_metric` | `float | None` | 否 | `None` | 最佳指标值 | - |
| `best_metric_name` | `str` | 否 | `'loss'` | 最佳指标名称 | - |
| `state_dict_path` | `str` | 否 | `''` | 状态字典存储路径 | - |
| `optimizer_state_path` | `str` | 否 | `''` | 优化器状态存储路径 | - |
| `rng_state` | `dict[str, Any] | None` | 否 | `None` | 随机数生成器状态 | - |
| `checkpoint_type` | `CheckpointType` | 是 | `-` | 检查点类型 | - |
| `metrics` | `dict[str, Any]` | 是 | `-` | 指标字典 | - |
| `metadata` | `dict[str, Any]` | 是 | `-` | 元数据 | - |

### `MemoryEntryAddRequest`

添加 MemoryEntry 的请求体（白名单字段）。

memory_id / created_at / last_accessed / access_count / embedding_ref
由服务端管理。importance 约束在 [0, 1] 区间。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `content` | `str` | 否 | `''` | 记忆内容 | - |
| `memory_type` | `str` | 否 | `'observation'` | 记忆类型 | - |
| `importance` | `float` | 否 | `0.5` | 重要性权重 [0,1] | ≥ 0.0; ≤ 1.0 |
| `tags` | `list[str]` | 是 | `-` | 标签列表 | - |
| `metadata` | `dict[str, Any]` | 是 | `-` | 元数据 | - |

### `CheckpointRollbackRequest`

回滚到指定 Checkpoint 的请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `checkpoint_id` | `str` | 是 | `-` | 目标检查点ID（必填） | - |

### `ContextUpdateRequest`

更新 Agent 会话上下文的请求体。

updates 为动态字典，由 :meth:`update_context_increment` 内部处理。
允许直接传业务字段（无 updates 键时整体作为 updates）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `updates` | `dict[str, Any]` | 是 | `-` | 上下文更新字典 | - |

### `AgentCloneRequest`

克隆 Agent 状态的请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `target_agent_id` | `str` | 是 | `-` | 目标 Agent ID（必填） | - |

### `TokenRequest`

令牌请求模型。

用于 refresh_token 和 logout 端点，替换原 body: dict 弱验证。
两个字段均默认空字符串以兼容 logout 端点的可选语义；
refresh_token 端点会在函数体内显式校验非空。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `refresh_token` | `str` | 否 | `''` | 刷新令牌 | - |
| `access_token` | `str` | 否 | `''` | 访问令牌 | - |

### `StockModelRequest`

工件模型请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `length` | `float` | 是 | `-` | 工件长度 (mm) | - |
| `width` | `float` | 是 | `-` | 工件宽度 (mm) | - |
| `height` | `float` | 是 | `-` | 工件高度 (mm) | - |
| `x_offset` | `float` | 否 | `0.0` | X方向偏移 (mm) | - |
| `y_offset` | `float` | 否 | `0.0` | Y方向偏移 (mm) | - |
| `z_offset` | `float` | 否 | `0.0` | Z方向偏移 (mm) | - |

### `ToolpathSegmentRequest`

刀路段请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `type` | `str` | 是 | `-` | 段类型 (rapid/linear/arc) | - |
| `start_point` | `tuple[float, float, float]` | 是 | `-` | 起点坐标 (x, y, z) | - |
| `end_point` | `tuple[float, float, float]` | 是 | `-` | 终点坐标 (x, y, z) | - |
| `block_number` | `int` | 否 | `0` | NC程序段号 | - |
| `feed_rate` | `float | None` | 否 | `None` | 进给率 (mm/min) | - |
| `a_angle` | `float | None` | 否 | `None` | A轴角度 (度) | - |
| `c_angle` | `float | None` | 否 | `None` | C轴角度 (度) | - |

### `CollisionCheckRequest`

碰撞检测请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `stock` | `StockModelRequest` | 是 | `-` | 工件模型 | - |
| `segments` | `list[ToolpathSegmentRequest]` | 是 | `-` | 刀路段列表 | - |
| `safe_z_height` | `float` | 否 | `10.0` | 安全Z高度 (mm) | - |
| `mode` | `str` | 否 | `'3axis'` | 检测模式 (3axis/5axis) | - |
| `workspace_limits` | `dict[str, float] | None` | 否 | `None` | 机床工作空间限制 | - |

### `CollisionCheckResponse`

碰撞检测响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `code` | `int` | 否 | `0` | 状态码 (0=成功) | - |
| `message` | `str` | 否 | `'OK'` | 状态消息 | - |
| `data` | `dict[str, Any]` | 是 | `-` | 碰撞检测报告 | - |

### `SetUnitPriceRequest`

设置单价请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `key` | `str` | 是 | `-` | 单价键名 | 最小长度: 1 |
| `value` | `float` | 是 | `-` | 单价数值 | - |

### `SetBudgetPolicyRequest`

设置预算策略请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `level` | `str` | 否 | `'global'` | 预算层级 | - |
| `scope_id` | `str` | 否 | `'default'` | 范围ID | - |
| `resource_type` | `str` | 否 | `'total_cost'` | 资源类型 | - |
| `limit` | `float` | 否 | `100.0` | 预算上限 | - |
| `period` | `str` | 否 | `'daily'` | 预算周期 | - |
| `warning_threshold` | `float` | 否 | `0.8` | 预警阈值 | - |
| `hard_stop` | `bool` | 否 | `True` | 是否硬性停止 | - |
| `auto_notify` | `bool` | 否 | `True` | 是否自动通知 | - |
| `enabled` | `bool` | 否 | `True` | 是否启用 | - |

### `AdjustBudgetRequest`

调整预算请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `level` | `str` | 是 | `-` | 预算层级 | - |
| `scope_id` | `str` | 否 | `'default'` | 范围ID | - |
| `resource_type` | `str` | 是 | `-` | 资源类型 | - |
| `new_limit` | `float` | 是 | `-` | 新预算上限 | - |
| `reason` | `str` | 否 | `''` | 调整原因 | - |
| `adjusted_by` | `str` | 否 | `'admin'` | 调整人 | - |

### `CheckBudgetRequest`

检查预算请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `level` | `str` | 否 | `'global'` | 预算层级 | - |
| `scope_id` | `str` | 否 | `'default'` | 范围ID | - |
| `resource_type` | `str` | 否 | `'total_cost'` | 资源类型 | - |
| `planned_usage` | `float` | 否 | `0.0` | 计划用量 | - |

### `CheckBudgetCascadeRequest`

级联检查预算请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `agent_id` | `str` | 否 | `''` | Agent ID | - |
| `project_id` | `str` | 否 | `'default'` | 项目ID | - |
| `resource_type` | `str` | 否 | `'total_cost'` | 资源类型 | - |
| `planned_usage` | `float` | 否 | `0.0` | 计划用量 | - |

### `EnforceBudgetRequest`

强制预算请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `level` | `str` | 否 | `'global'` | 预算层级 | - |
| `scope_id` | `str` | 否 | `'default'` | 范围ID | - |
| `resource_type` | `str` | 否 | `'total_cost'` | 资源类型 | - |
| `planned_usage` | `float` | 否 | `0.0` | 计划用量 | - |

### `ResetBudgetPeriodRequest`

重置预算周期请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `level` | `str` | 是 | `-` | 预算层级 | - |
| `scope_id` | `str` | 否 | `'default'` | 范围ID | - |
| `resource_type` | `str` | 是 | `-` | 资源类型 | - |

### `DatasetSchemaModel`

DatasetSchema 的 Pydantic 模型版本（用于 API JSON 传输）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `fields` | `dict[str, dict[str, Any]]` | 是 | `-` |  | - |
| `primary_key` | `list[str]` | 是 | `-` |  | - |
| `metadata` | `dict[str, Any]` | 是 | `-` |  | - |

### `CreateDatasetRequest`

创建数据集请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `description` | `str` | 否 | `''` |  | - |
| `dataset_schema` | `DatasetSchemaModel` | 是 | `-` |  | 别名: schema |
| `owner_id` | `str` | 是 | `-` |  | - |

### `CommitVersionRequest`

提交版本请求体。

records 为空且 dataset_id 是 TrainingDataLake 适配器时，
适配器会自动从 lake 加载当前全部 records。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `records` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `version` | `str | None` | 否 | `None` |  | - |
| `lineage` | `LineageModel | None` | 否 | `None` |  | - |

### `LineageModel`

LineageRecord 的 API 入参模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `target` | `str` | 是 | `-` |  | - |
| `source_type` | `str` | 是 | `-` |  | - |
| `source_ref` | `str` | 是 | `-` |  | - |
| `inputs` | `list[str]` | 是 | `-` |  | - |
| `outputs` | `list[str]` | 是 | `-` |  | - |
| `operation` | `str` | 否 | `''` |  | - |
| `metadata` | `dict[str, Any]` | 是 | `-` |  | - |

### `MachineConnectRequest`

机床连接请求模型。

Attributes:
    machine_id: 机床唯一标识
    protocol: 通信协议（opcua / mtconnect）
    endpoint: 连接端点
    username: 认证用户名（可选）
    password: 认证密码（可选）
    device_name: MTConnect 设备名称（可选，默认 Device）

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `machine_id` | `str` | 是 | `-` | 机床唯一标识 | - |
| `protocol` | `ProtocolType` | 是 | `-` | 通信协议 | - |
| `endpoint` | `str` | 是 | `-` | 连接端点 | - |
| `username` | `str | None` | 否 | `None` | 认证用户名 | - |
| `password` | `str | None` | 否 | `None` | 认证密码 | - |
| `device_name` | `str | None` | 否 | `'Device'` | MTConnect 设备名称 | - |

### `NCSendRequest`

NC 程序发送请求模型。

Attributes:
    machine_id: 目标机床 ID
    program_path: 本地 NC 程序文件路径
    program_name: 机床端存储的程序名（可选，默认使用文件名）

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `machine_id` | `str` | 是 | `-` | 目标机床 ID | - |
| `program_path` | `str` | 是 | `-` | 本地 NC 程序文件路径 | - |
| `program_name` | `str | None` | 否 | `None` | 机床端存储的程序名（默认使用文件名） | - |

### `AutoConnectRequest`

自动探测连接请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `machine_id` | `str` | 是 | `-` | 机床唯一标识 | - |
| `endpoints` | `list[str]` | 是 | `-` | 候选端点列表，按优先级排序 | - |
| `username` | `str | None` | 否 | `None` | OPC UA 用户名 | - |
| `password` | `str | None` | 否 | `None` | OPC UA 密码 | - |
| `timeout` | `float` | 否 | `5.0` | 单端点连接超时 | > 0; ≤ 30 |

### `DiscoverRequest`

资产发现请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `subnet` | `str` | 否 | `'192.168.1'` | 子网前缀 | - |
| `timeout` | `float` | 否 | `0.3` | 单端口扫描超时 | > 0; ≤ 2 |

### `DocumentCreate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `title` | `str` | 是 | `-` |  | - |
| `category` | `str` | 是 | `-` |  | - |
| `version` | `str` | 否 | `'v1.0'` |  | - |
| `author` | `str` | 是 | `-` |  | - |
| `content` | `str | None` | 否 | `None` |  | - |
| `tags` | `list[str]` | 否 | `[]` |  | - |
| `status` | `str` | 否 | `'待审核'` |  | - |

### `DocumentUpdate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `title` | `str | None` | 否 | `None` |  | - |
| `category` | `str | None` | 否 | `None` |  | - |
| `version` | `str | None` | 否 | `None` |  | - |
| `author` | `str | None` | 否 | `None` |  | - |
| `content` | `str | None` | 否 | `None` |  | - |
| `tags` | `list[str] | None` | 否 | `None` |  | - |
| `status` | `str | None` | 否 | `None` |  | - |

### `DxfProcessRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `dxf_path` | `str` | 是 | `-` |  | - |
| `output_dir` | `str | None` | 否 | `None` |  | - |
| `postprocessor` | `str | None` | 否 | `'fanuc_0i'` |  | - |
| `user_id` | `str | None` | 否 | `None` |  | - |

### `DxfBatchRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `dxf_paths` | `list[str]` | 是 | `-` |  | 最小长度: 1; 最大长度: 20 |
| `output_dir` | `str | None` | 否 | `None` |  | - |
| `postprocessor` | `str | None` | 否 | `'fanuc_0i'` |  | - |
| `user_id` | `str | None` | 否 | `None` |  | - |

### `DxfE2EFixtureRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `fixtures_dir` | `str` | 否 | `'data/test_fixtures'` |  | - |
| `output_dir` | `str` | 否 | `'data/outputs/e2e'` |  | - |
| `postprocessor` | `str` | 否 | `'fanuc_0i'` |  | - |
| `user_id` | `str | None` | 否 | `'e2e_runner'` |  | - |

### `WearStateRequest`

刀具磨损状态请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `tool_id` | `int` | 是 | `-` | 刀具 ID | - |
| `wear_amount` | `float` | 是 | `-` | 当前磨损量 VB (mm) | ≥ 0.0 |
| `usage_time` | `float` | 是 | `-` | 累计使用时间 (分钟) | ≥ 0.0 |
| `wear_threshold` | `float` | 否 | `0.3` | 更换阈值 (mm) | > 0.0 |
| `material_type` | `str` | 否 | `'steel_45'` | 材料类型 | - |
| `tool_type` | `str` | 否 | `'carbide'` | 刀具类型 | - |
| `tool_diameter` | `float` | 否 | `10.0` | 刀具直径 (mm) | > 0.0 |
| `flute_count` | `int` | 否 | `2` | 齿数 | ≥ 1 |

### `CurrentParametersRequest`

当前切削参数请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `cutting_speed` | `float` | 是 | `-` | 切削速度 (m/min) | > 0.0 |
| `feed_rate` | `float` | 是 | `-` | 每转进给 (mm/rev) | > 0.0 |
| `depth_of_cut` | `float` | 是 | `-` | 轴向切深 ap (mm) | > 0.0 |
| `width_of_cut` | `float` | 否 | `0.0` | 径向切深 ae (mm) | ≥ 0.0 |
| `spindle_rpm` | `float | None` | 否 | `None` | 主轴转速 (RPM, None 时由切削速度反算) | ≥ 0.0 |
| `coolant_flow` | `float` | 否 | `10.0` | 冷却液流量 (L/min) | ≥ 0.0 |

### `MachineCapabilities`

机床能力上限。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `max_spindle_speed` | `float | None` | 否 | `None` | 最大主轴转速 (RPM) | ≥ 0.0 |
| `max_feed_rate` | `float | None` | 否 | `None` | 最大进给速度 (mm/min) | ≥ 0.0 |
| `max_power` | `float | None` | 否 | `None` | 最大功率 (kW) | ≥ 0.0 |
| `max_torque` | `float | None` | 否 | `None` | 最大扭矩 (N·m) | ≥ 0.0 |

### `CalibrationInput`

实时磨损校正入参（启用「实时信号 → 磨损模型在线校正 → 决策」闭环）。

与 ToolWearPredictor.calibrate_with_real_time_data 对齐。
schema 与 SignalFusionKnowledgeBase.SignalSample.sensor_features 完全兼容。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `real_time_wear` | `float` | 是 | `-` | 实测磨损量 (mm) | ≥ 0.0 |
| `sensor_features` | `dict[str, float]` | 是 | `-` | 传感器特征字典，支持 vibration_rms (g) / cutting_force (N) / temperature (°C) / acoustic_emission 等字段 | - |
| `elapsed_time` | `float` | 是 | `-` | 自上次校正以来的加工时间 (min) | > 0.0 |

### `DecideRequest`

参数调整决策请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `wear` | `WearStateRequest` | 是 | `-` |  | - |
| `current` | `CurrentParametersRequest` | 是 | `-` |  | - |
| `machine_capabilities` | `MachineCapabilities | None` | 否 | `None` | 机床能力上限（None 使用默认） | - |
| `optimization_goal` | `str` | 否 | `'tool_life'` | 优化目标：efficiency / tool_life / surface_finish | - |
| `calibration` | `CalibrationInput | None` | 否 | `None` | 可选实时校正入参。提供时启用 EWMA 校正闭环，用校正后磨损值驱动决策；未提供时走原始磨损值路径 | - |

### `AdjustmentDecisionInput`

P2-批次2 修复：``RewriteNCRequest.decision`` 的强类型替代裸 dict。

原 ``decision: dict[str, Any]`` 允许任意键穿透到 ``AdjustmentDecision``
构造，存在字段缺失/类型错误仅在运行时暴露的风险。改为 Pydantic 子模型
后，请求体在进入端点前即完成结构与类型校验。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `strategy` | `Literal[no_adjustment, slight_compensation, moderate_compensation, aggressive_compensation, replace_tool]` | 否 | `'no_adjustment'` |  | - |
| `urgency` | `Literal[normal, warning, critical]` | 否 | `'normal'` |  | - |
| `new_cutting_speed` | `float` | 否 | `0.0` |  | - |
| `new_feed_rate` | `float` | 否 | `0.0` |  | - |
| `new_depth_of_cut` | `float` | 否 | `0.0` |  | - |
| `new_spindle_rpm` | `float` | 否 | `0.0` |  | - |
| `new_feed_rate_mm_min` | `float` | 否 | `0.0` |  | - |
| `life_extension_pct` | `float` | 否 | `0.0` |  | - |
| `suggestions` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `warnings` | `list[str]` | 是 | `-` |  | - |
| `reasoning` | `list[str]` | 是 | `-` |  | - |

### `RewriteNCRequest`

NC 代码改写请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `nc_code` | `str` | 是 | `-` | NC/G 代码文本 | 最小长度: 1 |
| `decision` | `AdjustmentDecisionInput` | 是 | `-` | 由 /decide 返回的决策对象 | - |
| `controller_type` | `str` | 否 | `'fanuc'` | 控制器方言 (fanuc/siemens/heidenhain) | - |
| `apply_to_motion_only` | `bool` | 否 | `True` | 仅改写切削进给段（G01/G02/G03），跳过 G00 | - |

### `ClosedLoopRequest`

端到端闭环请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `wear` | `WearStateRequest` | 是 | `-` |  | - |
| `current` | `CurrentParametersRequest` | 是 | `-` |  | - |
| `nc_code` | `str` | 是 | `-` | 待改写的 NC/G 代码文本 | 最小长度: 1 |
| `machine_capabilities` | `MachineCapabilities | None` | 否 | `None` |  | - |
| `optimization_goal` | `str` | 否 | `'tool_life'` |  | - |
| `controller_type` | `str` | 否 | `'fanuc'` |  | - |
| `apply_to_motion_only` | `bool` | 否 | `True` |  | - |
| `calibration` | `CalibrationInput | None` | 否 | `None` | 可选实时校正入参。提供时启用 EWMA 校正闭环，用校正后磨损值驱动决策与 NC 改写 | - |

### `CalibrateWearRequest`

实时磨损校正请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `real_time_wear` | `float` | 是 | `-` | 实测磨损量 (mm) | ≥ 0.0 |
| `sensor_features` | `dict[str, float]` | 是 | `-` | 传感器特征（vibration_rms / cutting_force / temperature 等） | - |
| `elapsed_time` | `float` | 是 | `-` | 自上次校正以来的时间 (分钟) | > 0.0 |
| `input_parameters` | `dict[str, Any]` | 是 | `-` | 当前切削参数（cutting_speed/feed_rate/depth_of_cut/material_type/tool_type/tool_diameter/current_wear） | - |

### `EquipmentUpdateRequest`

更新设备状态和指标的请求体（白名单字段）。

与 service 层 ``_EQUIPMENT_ALLOWED_FIELDS`` 保持一致。
所有字段可选，至少传一个。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `status` | `str | None` | 否 | `None` | 设备状态: 运行中/待机/维护中/故障 | - |
| `temperature` | `float | None` | 否 | `None` | 温度 | - |
| `vibration` | `float | None` | 否 | `None` | 振动 | - |
| `rpm` | `float | None` | 否 | `None` | 转速 | - |
| `power` | `float | None` | 否 | `None` | 功率 | - |

### `AlarmStatusUpdateRequest`

更新告警状态的请求体。

status 必须为 ``_ALARM_VALID_STATUSES`` 之一（service 层二次校验）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `status` | `str` | 是 | `-` | 告警状态: 未处理/已确认/已解决 | - |

### `MaintenancePlanUpdateRequest`

更新维护计划的请求体（白名单字段）。

与 service 层 ``_MAINTENANCE_ALLOWED_FIELDS`` 保持一致。
所有字段可选，至少传一个。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `title` | `str | None` | 否 | `None` | 计划标题 | - |
| `type` | `str | None` | 否 | `None` | 计划类型 | - |
| `frequency` | `str | None` | 否 | `None` | 维护频率 | - |
| `last_date` | `str | None` | 否 | `None` | 上次维护日期 | - |
| `next_date` | `str | None` | 否 | `None` | 下次维护日期 | - |
| `status` | `str | None` | 否 | `None` | 计划状态 | - |

### `GenerateHiddenStateRequest`

生成隐状态投影解释请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_uri` | `str` | 是 | `-` | 模型 URI | 最小长度: 1; 最大长度: 256 |
| `source_snapshot_id` | `str | None` | 否 | `None` | 关联实验快照 ID（可选） | 最大长度: 64 |
| `projection_method` | `str` | 是 | `-` |  | - |
| `projection_dim` | `int` | 否 | `2` | 投影维度（2 或 3，默认 2） | ≥ 2; ≤ 3 |
| `max_frames` | `int` | 否 | `1000` | 最大帧数（超过则均匀采样） | ≥ 1; ≤ 10000 |
| `created_by` | `str | None` | 否 | `None` | 创建者（user_id 或 plugin_id） | 最大长度: 128 |

### `GenerateGateDynamicsRequest`

生成门控动力学解释请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_uri` | `str` | 是 | `-` | 模型 URI | 最小长度: 1; 最大长度: 256 |
| `source_snapshot_id` | `str | None` | 否 | `None` | 关联实验快照 ID（可选） | 最大长度: 64 |
| `anomaly_sigma` | `float` | 否 | `2.0` | 异常检测阈值（门控值超过 mean ± sigma*std 的帧，默认 2.0） | ≥ 1.0; ≤ 5.0 |
| `created_by` | `str | None` | 否 | `None` | 创建者（user_id 或 plugin_id） | 最大长度: 128 |

### `GenerateCounterfactualRequest`

生成反事实解释请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_uri` | `str` | 是 | `-` | 模型 URI | 最小长度: 1; 最大长度: 256 |
| `base_input` | `dict[str, float]` | 是 | `-` | 基准输入（特征名 → 值），至少 1 个特征 | - |
| `perturbed_feature` | `str` | 是 | `-` | 被扰动的特征名 | 最小长度: 1; 最大长度: 64 |
| `perturbation_range` | `list[float] | None` | 否 | `None` | 扰动值序列（如为空则按 perturbation_step 生成） | - |
| `perturbation_step` | `float` | 否 | `0.05` | 扰动步长（相对基准值的比例，默认 0.05 即 5%） | ≥ 0.01; ≤ 0.5 |
| `source_snapshot_id` | `str | None` | 否 | `None` | 关联实验快照 ID（可选） | 最大长度: 64 |
| `created_by` | `str | None` | 否 | `None` | 创建者（user_id 或 plugin_id） | 最大长度: 128 |

### `GenerateConfidenceRequest`

生成置信度分布解释请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_uri` | `str` | 是 | `-` | 模型 URI | 最小长度: 1; 最大长度: 256 |
| `input_data` | `dict[str, Any]` | 是 | `-` | 输入数据（特征名 → 值） | - |
| `sample_count` | `int` | 否 | `30` | MC dropout 采样次数（默认 30） | ≥ 5; ≤ 200 |
| `source_snapshot_id` | `str | None` | 否 | `None` | 关联实验快照 ID（可选） | 最大长度: 64 |
| `created_by` | `str | None` | 否 | `None` | 创建者（user_id 或 plugin_id） | 最大长度: 128 |

### `CompareExplanationsRequest`

对比两个解释请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `base_explanation_id` | `str` | 是 | `-` | 基准解释记录 ID | 最小长度: 1; 最大长度: 64 |
| `compared_explanation_id` | `str` | 是 | `-` | 对比解释记录 ID | 最小长度: 1; 最大长度: 64 |
| `comparison_type` | `str` | 是 | `-` |  | - |
| `created_by` | `str | None` | 否 | `None` | 创建者（user_id 或 plugin_id） | 最大长度: 128 |

### `MetricDefinition`

指标定义。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` | 指标名称 | - |
| `description` | `str` | 是 | `-` | 指标含义 | - |
| `unit` | `str` | 是 | `-` | 单位 | - |
| `range` | `str` | 是 | `-` | 取值范围 | - |
| `calculation` | `str` | 是 | `-` | 计算方式 | - |

### `FlywheelStatusResponse`

飞轮状态响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `status` | `str` | 是 | `-` | 飞轮状态: healthy / warning / critical | - |
| `data_volume` | `int` | 是 | `-` | 加工记录数（条） | - |
| `model_quality` | `float` | 是 | `-` | 模型质量（%，0-100） | - |
| `adoption_rate` | `float` | 是 | `-` | 用户采纳率（%，0-100） | - |
| `uncertainty_mean` | `float` | 是 | `-` | 不确定性均值（0-1） | - |
| `feedback_delay` | `float` | 是 | `-` | 回灌延迟（分钟） | - |
| `health_score` | `float` | 是 | `-` | 健康分数（0-100） | - |
| `timestamp` | `str` | 是 | `-` | 采集时间（ISO 8601） | - |

### `FlywheelReportResponse`

飞轮报告响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `report_type` | `str` | 是 | `-` |  | - |
| `generated_at` | `str` | 是 | `-` |  | - |
| `period` | `dict[str, str]` | 是 | `-` |  | - |
| `current_metrics` | `dict[str, Any]` | 是 | `-` |  | - |
| `trends` | `dict[str, Any]` | 是 | `-` |  | - |
| `summary` | `dict[str, Any]` | 是 | `-` |  | - |

### `MetricDefinitionsResponse`

指标定义列表响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `metrics` | `list[MetricDefinition]` | 是 | `-` |  | - |

### `GoalCreateRequest`

目标对齐创建请求模型。

字段对应 ``create_goal`` 端点原本从 ``data: dict`` 读取的键：
level/status 在端点内会再做枚举校验（GoalLevel / GoalStatus），
因此这里仅做基础字符串校验，避免重复实现枚举错误处理逻辑。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `level` | `str` | 否 | `'task'` | 目标层级: mission/strategic_goal/project/task | - |
| `status` | `str` | 否 | `'not_started'` | 目标状态: not_started/in_progress/completed/cancelled | - |
| `id` | `str | None` | 否 | `None` | 目标ID（不传则自动生成） | - |
| `name` | `str` | 否 | `''` | 目标名称 | - |
| `description` | `str` | 否 | `''` | 目标描述 | - |
| `parent_id` | `str | None` | 否 | `None` | 父目标ID（非 mission 必填） | - |

### `CreateApprovalRequest`

创建审批请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` | 任务 ID | - |
| `requester` | `str` | 是 | `-` | 请求人 | - |
| `context` | `dict` | 是 | `-` | 上下文 | - |
| `budget_amount` | `float` | 否 | `0.0` | 预算金额（必须 >=0） | ≥ 0.0 |
| `agent_role` | `str` | 否 | `'engineer'` | 代理角色 | - |

### `AssignApproverRequest`

指派审批人请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `approver_id` | `str | None` | 否 | `None` | 审批人 ID | - |

### `MakeDecisionRequest`

审批决策请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `approver_id` | `str` | 是 | `-` | 审批人 ID | - |
| `decision` | `str` | 是 | `-` | 决策: approved/rejected/escalated/request_info | - |
| `comment` | `str` | 否 | `''` | 备注 | - |

### `EscalateRequest`

审批升级请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `escalator_id` | `str` | 否 | `'system'` | 升级操作人 ID | - |
| `reason` | `str` | 否 | `''` | 升级原因 | - |

### `AssessRiskRequest`

风险评估请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `operation_id` | `str | None` | 否 | `None` | 操作 ID（None 自动生成） | - |
| `operation_type` | `str` | 是 | `-` | 操作类型（仅允许字母、数字、下划线、连字符） | 最小长度: 1; 最大长度: 100; 正则: `^[A-Za-z0-9_-]+$` |
| `context` | `dict` | 是 | `-` | 上下文 | - |
| `requester_role` | `str` | 否 | `'engineer'` | 请求人角色 | - |
| `budget_amount` | `float` | 否 | `0.0` | 预算金额（必须 >=0） | ≥ 0.0 |

### `EmergencyOverrideRequest`

紧急覆盖请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `request_id` | `str` | 是 | `-` | 关联的审批请求ID | - |
| `task_id` | `str` | 是 | `-` | 任务ID | - |
| `operator_id` | `str` | 是 | `-` | 操作员ID | - |
| `reason` | `str` | 是 | `-` | 紧急覆盖原因 | - |
| `emergency_type` | `str` | 否 | `'production_halt'` | 紧急类型 | - |

### `CompleteRetroactiveRequest`

完成事后审批请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `emergency_id` | `str | None` | 否 | `None` | 紧急操作 ID | - |

### `CreateDelegationRequest`

创建委托请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `delegator_id` | `str` | 是 | `-` | 委托人 ID | - |
| `delegate_id` | `str` | 是 | `-` | 被委托人 ID | - |
| `start_time` | `float` | 是 | `-` | 开始时间（Unix 时间戳） | - |
| `end_time` | `float` | 是 | `-` | 结束时间（Unix 时间戳） | - |
| `reason` | `str` | 否 | `''` | 委托原因 | - |

### `CreateScheduledTaskRequest`

创建调度任务请求

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` | 任务唯一标识符 | 最小长度: 1 |
| `agent_id` | `str` | 是 | `-` | 执行代理ID | 最小长度: 1 |
| `schedule` | `str` | 是 | `-` | Cron表达式（分 时 日 月 星期） | 最小长度: 1 |
| `task_type` | `str` | 是 | `-` | 任务类型（lnn_inference/lnn_training/lnn_analysis） | 最小长度: 1 |
| `params` | `dict[str, Any]` | 是 | `-` | 任务参数 | - |
| `metadata` | `dict[str, Any]` | 是 | `-` | 任务元数据 | - |
| `max_retries` | `int` | 否 | `3` | 最大重试次数 | ≥ 0; ≤ 10 |

### `TaskResponse`

任务响应

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `agent_id` | `str` | 是 | `-` |  | - |
| `schedule` | `str` | 是 | `-` |  | - |
| `task_type` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `last_run` | `float | None` | 否 | `None` |  | - |
| `next_run` | `float | None` | 否 | `None` |  | - |
| `retry_count` | `int` | 否 | `0` |  | - |
| `max_retries` | `int` | 否 | `3` |  | - |
| `params` | `dict[str, Any]` | 否 | `{}` |  | - |
| `metadata` | `dict[str, Any]` | 否 | `{}` |  | - |

### `BudgetCheckResponse`

预算检查响应

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `passed` | `bool` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `usages` | `list[dict[str, Any]]` | 否 | `[]` |  | - |
| `warnings` | `list[str]` | 否 | `[]` |  | - |
| `blocked_reasons` | `list[str]` | 否 | `[]` |  | - |

### `ExecutionResultResponse`

执行结果响应

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `duration_ms` | `float` | 是 | `-` |  | - |
| `result_data` | `dict[str, Any] | None` | 否 | `None` |  | - |
| `error_message` | `str | None` | 否 | `None` |  | - |
| `resource_usage` | `dict[str, Any]` | 否 | `{}` |  | - |

### `CreateJobRequest`

通用任务创建请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_type` | `str` | 是 | `-` | 任务类型（lnn_training/lnn_inference/data_processing 等） | - |
| `params` | `dict` | 是 | `-` | 任务参数 | - |
| `name` | `str | None` | 否 | `None` | 任务名称（并入 params.name） | 最大长度: 128 |
| `idempotency_key` | `str | None` | 否 | `None` | 幂等键 | 最大长度: 128 |

### `GraphQueryRequest`

``POST /query`` 请求体模型。

用 Pydantic 验证替代原始 dict，避免 ``**params`` 解包触发 TypeError。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `query_type` | `str` | 是 | `-` | 查询类型，如 search_nodes | - |
| `params` | `dict[str, Any]` | 是 | `-` | 查询参数键值对 | - |

### `ProviderCreateRequest`

创建/更新 Provider 请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `provider_id` | `str` | 是 | `-` | Provider 唯一标识 | - |
| `name` | `str` | 是 | `-` | 显示名称 | - |
| `provider_type` | `str` | 是 | `-` | Provider 类型（ollama/openai/...） | - |
| `base_url` | `str` | 否 | `''` | API 基地址 | - |
| `api_key` | `str` | 否 | `''` | API Key（云端 Provider 用，明文传入，服务端加密存储） | - |
| `default_model` | `str` | 否 | `''` | 默认模型名称 | - |
| `timeout` | `int` | 否 | `60` |  | ≥ 5; ≤ 600 |
| `max_retries` | `int` | 否 | `3` |  | ≥ 0; ≤ 10 |
| `retry_delay` | `float` | 否 | `1.0` |  | ≥ 0.0; ≤ 30.0 |
| `enabled` | `bool` | 否 | `True` |  | - |
| `priority` | `int` | 否 | `0` |  | ≥ 0; ≤ 100 |
| `capabilities` | `list[str]` | 是 | `-` | 能力标签列表 | - |
| `extra` | `dict[str, Any]` | 是 | `-` |  | - |

### `ProviderUpdateRequest`

部分更新 Provider 请求体（所有字段可选）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str | None` | 否 | `None` |  | - |
| `base_url` | `str | None` | 否 | `None` |  | - |
| `api_key` | `str | None` | 否 | `None` | 留空表示不更新；显式传空串表示清除 | - |
| `default_model` | `str | None` | 否 | `None` |  | - |
| `timeout` | `int | None` | 否 | `None` |  | ≥ 5; ≤ 600 |
| `max_retries` | `int | None` | 否 | `None` |  | ≥ 0; ≤ 10 |
| `retry_delay` | `float | None` | 否 | `None` |  | ≥ 0.0; ≤ 30.0 |
| `enabled` | `bool | None` | 否 | `None` |  | - |
| `priority` | `int | None` | 否 | `None` |  | ≥ 0; ≤ 100 |
| `capabilities` | `list[str] | None` | 否 | `None` |  | - |
| `extra` | `dict[str, Any] | None` | 否 | `None` |  | - |

### `ChatTestRequest`

Provider 调用测试请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `messages` | `list[dict[str, str]]` | 是 | `-` | 消息列表，例如 [{'role':'user','content':'hello'}] | - |
| `max_tokens` | `int` | 否 | `256` |  | ≥ 1; ≤ 8192 |
| `temperature` | `float` | 否 | `0.7` |  | ≥ 0.0; ≤ 2.0 |
| `model` | `str | None` | 否 | `None` | 可选，覆盖默认模型 | - |

### `MaterialCreate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `code` | `str` | 是 | `-` | 物料编码 | 最小长度: 1; 最大长度: 64 |
| `name` | `str` | 是 | `-` | 名称 | 最小长度: 1; 最大长度: 128 |
| `spec` | `str` | 否 | `''` | 规格 | 最大长度: 256 |
| `category` | `str` | 否 | `'原材料'` | 分类: 原材料/半成品/成品 | 最大长度: 32 |
| `quantity` | `int` | 否 | `0` | 库存数量 | ≥ 0 |
| `safe_quantity` | `int` | 否 | `0` | 安全库存 | ≥ 0 |
| `status` | `str` | 否 | `'正常'` | 状态: 正常/低库存/缺货 | 最大长度: 16 |
| `location` | `str` | 否 | `''` | 库位 | 最大长度: 64 |
| `unit` | `str` | 否 | `''` | 单位 | 最大长度: 16 |
| `supplier` | `str` | 否 | `''` | 供应商 | 最大长度: 128 |

### `MaterialUpdate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `code` | `str | None` | 否 | `None` | 物料编码 | 最大长度: 64 |
| `name` | `str | None` | 否 | `None` | 名称 | 最大长度: 128 |
| `spec` | `str | None` | 否 | `None` | 规格 | 最大长度: 256 |
| `category` | `str | None` | 否 | `None` | 分类 | 最大长度: 32 |
| `quantity` | `int | None` | 否 | `None` | 库存数量 | ≥ 0 |
| `safe_quantity` | `int | None` | 否 | `None` | 安全库存 | ≥ 0 |
| `status` | `str | None` | 否 | `None` | 状态 | 最大长度: 16 |
| `location` | `str | None` | 否 | `None` | 库位 | 最大长度: 64 |
| `unit` | `str | None` | 否 | `None` | 单位 | 最大长度: 16 |
| `supplier` | `str | None` | 否 | `None` | 供应商 | 最大长度: 128 |

### `StockInRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `quantity` | `int` | 是 | `-` | 入库数量 | > 0; ≤ 100000 |
| `remark` | `str | None` | 否 | `None` | 入库备注 | 最大长度: 200 |

### `PurchaseRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `quantity` | `int` | 是 | `-` | 采购数量 | > 0; ≤ 100000 |
| `supplier` | `str | None` | 否 | `None` | 供应商 | 最大长度: 128 |

### `RecommendRequest`

参数推荐请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `material` | `str` | 是 | `-` |  | 最小长度: 1; 最大长度: 64 |
| `machining_type` | `str` | 否 | `'milling'` |  | 最大长度: 32 |
| `tool_id` | `str` | 否 | `''` |  | 最大长度: 64 |
| `target` | `OptimizationTarget` | 是 | `-` |  | - |

### `EvaluateRequest`

实测结果评估请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `cycle_time_s` | `float | None` | 否 | `None` |  | > 0 |
| `tool_wear_percent` | `float | None` | 否 | `None` |  | ≥ 0; ≤ 100 |
| `surface_roughness_ra` | `float | None` | 否 | `None` |  | ≥ 0 |
| `result` | `str` | 否 | `'ok'` |  | 正则: `^(ok|rework|scrap)$` |

### `CompareRequest`

A/B 对比请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `a_results` | `list[dict]` | 是 | `-` |  | 最小长度: 1 |
| `b_results` | `list[dict]` | 是 | `-` |  | 最小长度: 1 |

### `ExecutionRecordRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` | Task ID | - |
| `branch_id` | `str` | 是 | `-` | Branch ID | - |
| `elements` | `dict[str, Any]` | 是 | `-` | Execution elements | - |
| `conditions` | `dict[str, Any]` | 是 | `-` | Execution conditions | - |
| `metrics` | `dict[str, Any]` | 是 | `-` | Execution metrics | - |

### `TemplateReadRequest`

读取模板内容请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `dialect_id` | `str` | 是 | `-` | 方言 id | - |
| `method` | `str` | 是 | `-` | 模板方法名（如 format_header） | - |

### `PreviewRequest`

NC 输出预览请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `dialect_id` | `str` | 是 | `-` | 方言 id（声明镜像或内置） | - |
| `program_number` | `int` | 否 | `1000` | 程序号 | ≥ 1; ≤ 9999 |
| `safe_z_height` | `float` | 否 | `80.0` | 安全高度 | > 0 |
| `decimal_places` | `int` | 否 | `3` | 小数位数 | ≥ 0; ≤ 6 |

### `CreateDialectRequest`

新建方言请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `id` | `str` | 是 | `-` | 方言 id（小写字母/数字/下划线） | 正则: `^[a-z0-9_]{3,64}$` |
| `name` | `str` | 是 | `-` | 可读名称 | 最小长度: 1; 最大长度: 120 |
| `extends` | `str` | 是 | `-` | 继承的基类方言 id（如 fanuc_0i） | - |
| `description` | `str` | 否 | `''` | 描述 | 最大长度: 500 |
| `author` | `str` | 否 | `''` | 作者 | 最大长度: 120 |

### `SaveTemplateRequest`

保存模板内容请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `dialect_id` | `str` | 是 | `-` | 方言 id | - |
| `method` | `str` | 是 | `-` | 模板方法名（如 format_header） | - |
| `content` | `str` | 是 | `-` | 模板内容（Jinja2） | - |
| `max_length` | `int` | 是 | `-` | 模板最大字节数（防超大文件） | - |

### `SaveParamsRequest`

保存方言参数请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `dialect_id` | `str` | 是 | `-` | 方言 id | - |
| `params` | `dict[str, Any]` | 是 | `-` | 方言自己的参数（覆盖继承值） | - |

### `ExplainProcessRequest`

工艺规划解释请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `process_plan` | `dict[str, Any]` | 是 | `-` | 工艺规划 JSON | - |
| `user_question` | `str` | 否 | `''` | 用户上下文问题 | - |
| `material` | `str` | 否 | `''` | 工件材料 | - |
| `blank_size` | `str` | 否 | `''` | 毛坯尺寸描述 | - |
| `feature_count` | `int | None` | 否 | `None` | 加工特征数（None 自动推断） | ≥ 0 |
| `session_id` | `str | None` | 否 | `None` | 会话 ID（None 新建） | - |

### `ExplainNCRequest`

NC 代码解释请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `nc_code` | `str` | 是 | `-` | NC/G 代码文本 | 最小长度: 1 |
| `controller_type` | `str` | 否 | `'fanuc'` | 控制器类型（fanuc/siemens/heidenhain 等） | - |
| `user_question` | `str` | 否 | `''` | 用户上下文问题 | - |
| `session_id` | `str | None` | 否 | `None` | 会话 ID（None 新建） | - |

### `ChatRequest`

多轮对话请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `message` | `str` | 是 | `-` | 用户消息 | 最小长度: 1 |
| `session_id` | `str | None` | 否 | `None` | 会话 ID（None 新建） | - |

### `ProcessStepCreate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `sequence` | `int` | 是 | `-` |  | - |
| `name` | `str` | 是 | `-` |  | - |
| `work_center` | `str` | 是 | `-` |  | - |
| `hours` | `int` | 是 | `-` |  | - |
| `equipment` | `str | None` | 否 | `None` |  | - |
| `tooling` | `str | None` | 否 | `None` |  | - |

### `ProcessRouteCreate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `part_type` | `str` | 是 | `-` |  | - |
| `status` | `str` | 否 | `'草稿'` |  | - |
| `description` | `str | None` | 否 | `None` |  | - |
| `steps` | `list[ProcessStepCreate]` | 否 | `[]` |  | - |

### `ProcessRouteUpdate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str | None` | 否 | `None` |  | - |
| `part_type` | `str | None` | 否 | `None` |  | - |
| `status` | `str | None` | 否 | `None` |  | - |
| `description` | `str | None` | 否 | `None` |  | - |
| `steps` | `list[ProcessStepCreate] | None` | 否 | `None` |  | - |

### `WorkOrderUpdate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `product_name` | `str | None` | 否 | `None` |  | - |
| `planned_qty` | `int | None` | 否 | `None` |  | - |
| `completed_qty` | `int | None` | 否 | `None` |  | - |
| `status` | `str | None` | 否 | `None` |  | - |
| `priority` | `str | None` | 否 | `None` |  | - |
| `start_date` | `date | None` | 否 | `None` |  | - |
| `due_date` | `date | None` | 否 | `None` |  | - |

### `ExportProjectRequest`

导出项目请求体（JSON）.

将项目及其引用资源打包为 ``.lomo`` 文件。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `project_id` | `str` | 是 | `-` | 源项目 ID | 最小长度: 1; 最大长度: 64 |
| `exported_by` | `str` | 是 | `-` | 导出者（user_id 或 plugin_id） | 最小长度: 1; 最大长度: 128 |
| `output_dir` | `str` | 否 | `''` | 输出目录（空字符串表示使用服务层默认目录） | 最大长度: 512 |
| `content_policy` | `str` | 是 | `-` |  | - |
| `include_datasets` | `bool` | 否 | `True` | 是否打包数据集资源 | - |
| `include_models` | `bool` | 否 | `True` | 是否打包模型产物资源 | - |
| `include_workflows` | `bool` | 否 | `True` | 是否打包工作流定义 | - |
| `include_configs` | `bool` | 否 | `True` | 是否打包配置规格 | - |
| `include_snapshots` | `bool` | 否 | `True` | 是否打包实验快照元数据 | - |
| `include_lineage` | `bool` | 否 | `True` | 是否打包血缘记录 | - |
| `max_file_size_bytes` | `int` | 是 | `-` | small_files_only 策略下的文件大小阈值（字节，默认 10MB） | ≥ 1 |
| `output_filename` | `str` | 否 | `''` | 自定义输出文件名（不含路径，空字符串使用默认模板） | 最大长度: 256 |

### `CreateProjectRequest`

创建项目请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` | 项目显示名 | 最小长度: 1; 最大长度: 256 |
| `description` | `str` | 否 | `''` | 项目描述 | 最大长度: 2048 |
| `author` | `str` | 否 | `''` | 项目作者 | 最大长度: 128 |
| `remote_url` | `str` | 否 | `''` | 远端仓库 URL（空表示纯本地仓库） | 最大长度: 1024 |
| `branch` | `str` | 否 | `'main'` | 初始分支名 | 最大长度: 128 |
| `initial_commit` | `bool` | 否 | `True` | 是否在创建时生成首个 commit | - |

### `CloneProjectRequest`

克隆远端项目请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `remote_url` | `str` | 是 | `-` | 远端仓库 URL | 最小长度: 1; 最大长度: 1024 |
| `name` | `str` | 是 | `-` | 项目显示名 | 最小长度: 1; 最大长度: 256 |
| `branch` | `str` | 否 | `'main'` | 检出分支名 | 最大长度: 128 |
| `description` | `str` | 否 | `''` | 项目描述 | 最大长度: 2048 |
| `author` | `str` | 否 | `''` | 项目作者 | 最大长度: 128 |

### `CommitRequest`

提交变更请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `message` | `str` | 是 | `-` | commit message | 最小长度: 1; 最大长度: 2048 |

### `AddResourceRequest`

添加资源引用请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `resource_type` | `str` | 是 | `-` |  | - |
| `resource_uri` | `str` | 是 | `-` | 资源 URI（如 dataset://phm2010/v3） | 最大长度: 512 |
| `sync_strategy` | `str` | 是 | `-` |  | - |
| `metadata` | `dict[str, Any]` | 是 | `-` | 附加元数据 | - |

### `QualityRecordCreate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `batch_no` | `str` | 是 | `-` |  | - |
| `inspection_type` | `str` | 是 | `-` |  | - |
| `result` | `str` | 是 | `-` |  | - |
| `inspector` | `str` | 是 | `-` |  | - |
| `notes` | `str | None` | 否 | `None` |  | - |

### `QualityAnomalyCreate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `record_id` | `str` | 是 | `-` |  | - |
| `anomaly_type` | `str` | 是 | `-` |  | - |
| `description` | `str | None` | 否 | `None` |  | - |
| `severity` | `str` | 是 | `-` |  | - |

### `UpsertDatasetReadmeRequest`

更新数据集 README 请求体（upsert 语义）.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `readme_md` | `str` | 是 | `-` | markdown README 内容 | 最小长度: 1; 最大长度: 200000 |
| `updated_by` | `str` | 是 | `-` | 最后更新者（user_id 或 plugin_id） | 最小长度: 1; 最大长度: 128 |
| `version` | `str | None` | 否 | `None` | 版本号（如 1.0.0），不传则更新数据集级 README | - |

### `RegisterModelRequest`

注册新模型产物请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_uri` | `str` | 是 | `-` | 模型 URI（model://<name>/<version>），全局唯一 | 最小长度: 1; 最大长度: 512 |
| `name` | `str` | 是 | `-` | 模型显示名 | 最小长度: 1; 最大长度: 128 |
| `model_type` | `str` | 是 | `-` |  | - |
| `version` | `str` | 是 | `-` | semver 版本号（如 1.0.0） | 最小长度: 1; 最大长度: 32 |
| `framework` | `str` | 是 | `-` | 框架版本（如 torch-2.1.0） | 最小长度: 1; 最大长度: 64 |
| `storage_uri` | `str` | 是 | `-` | 模型文件存储位置 | 最小长度: 1; 最大长度: 512 |
| `owner_id` | `str` | 是 | `-` | 所有者 ID | 最小长度: 1; 最大长度: 128 |
| `readme_md` | `str` | 否 | `''` | markdown README | 最大长度: 200000 |
| `tags` | `list[str]` | 是 | `-` | 标签数组 | - |
| `metrics` | `dict[str, Any]` | 是 | `-` | 初始指标快照（如 accuracy/loss） | - |
| `status` | `str` | 是 | `-` |  | - |

### `UpdateModelRequest`

更新模型卡片请求体（部分更新，仅非 None 字段被写入）.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `readme_md` | `str | None` | 否 | `None` | markdown README | 最小长度: 1; 最大长度: 200000 |
| `tags` | `list[str] | None` | 否 | `None` | 标签数组 | - |
| `status` | `str | None` | 否 | `None` |  | - |
| `metrics` | `dict[str, Any] | None` | 否 | `None` | 覆盖当前指标快照（不会追加到 history，请用 POST /metrics 追加） | - |
| `framework` | `str | None` | 否 | `None` | 框架版本 | 最小长度: 1; 最大长度: 64 |
| `storage_uri` | `str | None` | 否 | `None` | 模型文件存储位置 | 最小长度: 1; 最大长度: 512 |

### `AppendModelMetricsRequest`

追加模型指标记录请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `metrics` | `dict[str, Any]` | 是 | `-` | 指标字典（如 {'accuracy': 0.95, 'loss': 0.05}） | - |
| `timestamp` | `str | None` | 否 | `None` | 自定义时间戳（ISO8601），不传则使用服务器当前时间 | - |

### `SafetyConstraintsModel`

安全约束规格（与 ``SafetyConstraintsSpec`` 对齐）.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `max_chatter_probability` | `float` | 否 | `0.3` | 最大允许颤振概率 [0, 1]，默认 0.3 | ≥ 0.0; ≤ 1.0 |
| `max_tool_wear_increment` | `float` | 否 | `0.01` | 最大允许刀具磨损增量 (mm/步)，默认 0.01 | > 0.0 |
| `min_surface_quality` | `float` | 否 | `0.8` | 最小表面质量 [0, 1]，默认 0.8 | ≥ 0.0; ≤ 1.0 |

### `RLActRequestModel`

RL 决策请求体.

与 ``app.contracts.rl_agent.RLActRequest`` 对齐，但使用 Pydantic
以获得自动校验和 OpenAPI 文档。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `current_state` | `dict[str, float]` | 是 | `-` | 当前加工状态（字段名见 StateField，至少包含全部 8 个状态字段） | - |
| `candidate_actions` | `list[dict[str, float]]` | 是 | `-` | 候选动作集（至少 1 个，每个动作含 4 个 delta 字段） | 最小长度: 1 |
| `optimization_target` | `str` | 是 | `-` |  | - |
| `safety_constraints` | `SafetyConstraintsModel | None` | 否 | `None` | 安全约束规格（为空则使用默认值） | - |
| `model_uri` | `str` | 否 | `'model://rl_agent/1.0.0'` | RL 策略模型 URI | 最小长度: 1; 最大长度: 256 |

### `TrainingStartRequestModel`

启动训练请求体.

与 ``app.contracts.rl_agent.TrainingStartRequest`` 对齐。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `max_steps` | `int` | 否 | `100000` | 最大训练步数（1000 ~ 1000000，默认 100000） | ≥ 1000; ≤ 1000000 |
| `seed` | `int | None` | 否 | `None` | 随机种子（为空则使用训练器默认 42） | ≥ 0 |
| `algorithm` | `str` | 是 | `-` |  | - |
| `optimization_target` | `str` | 是 | `-` |  | - |

### `SignalSampleRequest`

信号样本注册请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `signal_type` | `str` | 是 | `-` | 信号类型 | - |
| `source` | `str` | 是 | `-` | 数据源标识 | - |
| `features` | `list[float]` | 是 | `-` | 9 维特征向量 | 最小长度: 1 |
| `sensor_features` | `dict[str, float]` | 是 | `-` | 传感器读数（与 ToolWearPredictor 对齐） | - |
| `process_context` | `dict[str, Any]` | 是 | `-` | 工艺上下文 | - |
| `machine_id` | `str` | 否 | `''` | 机床 ID | - |
| `tool_id` | `int | None` | 否 | `None` | 刀具 ID | - |
| `material` | `str` | 否 | `''` | 工件材料 | - |
| `label` | `str` | 否 | `''` | 可选标签 | - |
| `sample_id` | `str | None` | 否 | `None` | 自定义样本 ID | - |
| `metadata` | `dict[str, Any]` | 是 | `-` | 额外元数据 | - |

### `BatchSamplesRequest`

批量注册请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `samples` | `list[SignalSampleRequest]` | 是 | `-` |  | 最小长度: 1; 最大长度: 500 |

### `RetrieveRequest`

相似样本检索请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `features` | `list[float]` | 是 | `-` | 9 维查询特征 | 最小长度: 1 |
| `signal_type` | `str | None` | 否 | `None` | 信号类型过滤 | - |
| `machine_id` | `str | None` | 否 | `None` | 机床 ID 过滤 | - |
| `material` | `str | None` | 否 | `None` | 材料过滤 | - |
| `tool_id` | `int | None` | 否 | `None` | 刀具 ID 过滤 | - |
| `top_k` | `int` | 否 | `10` | 返回前 K 个 | ≥ 1; ≤ 100 |

### `FuseRequest`

多源信号融合请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `sample_ids` | `list[str]` | 是 | `-` | 参与融合的样本 ID 列表（与 samples 二选一） | - |
| `samples` | `list[SignalSampleRequest]` | 是 | `-` | 直接传入样本数据（与 sample_ids 二选一） | - |
| `strategy` | `str` | 否 | `'weighted'` | 融合策略: weighted 或 attention | - |
| `weights` | `dict[str, float] | None` | 否 | `None` | 自定义权重（仅 weighted 策略） | - |

### `CorrelateWearRequest`

磨损关联请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `sample_ids` | `list[str]` | 是 | `-` | 信号样本 ID 列表 | - |
| `samples` | `list[SignalSampleRequest]` | 是 | `-` | 直接传入样本数据 | - |

### `CorrelateChatterRequest`

颤振关联请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `sample_ids` | `list[str]` | 是 | `-` | 信号样本 ID 列表 | - |
| `samples` | `list[SignalSampleRequest]` | 是 | `-` | 直接传入样本数据 | - |
| `process_context` | `dict[str, Any]` | 是 | `-` | 工艺上下文覆盖（优先级高于样本内的 process_context） | - |

### `SkillContentRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `skill_id` | `str` | 是 | `-` | 技能唯一标识符 | - |
| `content` | `str` | 是 | `-` | 技能 Markdown 完整内容 | - |
| `level` | `str` | 否 | `'project'` | 技能层级: global/project/agent | - |
| `sub_id` | `str | None` | 否 | `None` | 项目ID或代理ID | - |

### `SkillExportRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `skill_id` | `str` | 是 | `-` | 要导出的技能ID | - |

### `SkillImportRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `skill_package` | `dict[str, Any]` | 是 | `-` | 技能包数据 | - |
| `level` | `str` | 否 | `'project'` | 导入层级 | - |
| `sub_id` | `str | None` | 否 | `None` | 项目ID或代理ID | - |

### `SkillRatingRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `skill_id` | `str` | 是 | `-` | 技能ID | - |
| `rating` | `float` | 是 | `-` | 评分 (0-5) | ≥ 0; ≤ 5 |

### `SkillPublishRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `skill_id` | `str` | 是 | `-` | 技能ID | - |
| `author` | `str` | 是 | `-` | 发布者 | - |

### `SkillDownloadRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `skill_id` | `str` | 是 | `-` | 技能ID | - |
| `target_level` | `str` | 否 | `'project'` | 目标层级 | - |
| `target_sub_id` | `str | None` | 否 | `None` | 目标项目/代理ID | - |

### `SkillMarketplaceRateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `skill_id` | `str` | 是 | `-` | 技能ID | - |
| `rating` | `float` | 是 | `-` | 评分 (0-5) | ≥ 0; ≤ 5 |
| `agent_id` | `str` | 否 | `''` | 评分的代理ID | - |

### `CreateSnapshotRequest`

创建实验快照请求体。

config 中可包含 ``workflow_spec`` 字段（dict 形式的 WorkflowSpec），
用于支持后续一键复现。其余字段由调用方按实验实际填写。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `config` | `dict[str, Any]` | 是 | `-` | 实验配置，可包含 workflow_spec / hyperparams / seed 等 | - |
| `dataset_versions` | `list[str]` | 是 | `-` | 关联的数据集版本 URI 列表（dataset://<name>/<version>） | - |
| `model_uri` | `str` | 是 | `-` | 模型 URI，如 model://ltc/1.0.0 | - |
| `metrics` | `dict[str, float]` | 是 | `-` | 实验指标，如 {'mae': 0.123, 'r2': 0.956} | - |
| `created_by` | `str` | 是 | `-` | 创建者标识（用户 ID 或 agent ID） | - |
| `notes` | `str` | 否 | `''` | 备注信息 | - |

### `RegisterTaskRequest`

任务注册请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `id` | `str` | 是 | `-` | 任务 ID | - |
| `title` | `str` | 否 | `''` | 任务标题 | - |
| `description` | `str` | 否 | `''` | 任务描述 | - |
| `task_type` | `str` | 否 | `'execution'` | 任务类型 | - |
| `status` | `str` | 否 | `'pending'` | 任务状态 | - |
| `assigned_to` | `str | None` | 否 | `None` | 指派给 | - |
| `parent_goal_id` | `str | None` | 否 | `None` | 父目标 ID | - |
| `project_id` | `str | None` | 否 | `None` | 项目 ID | - |
| `required_gpu_memory` | `float` | 否 | `0.0` | 所需 GPU 显存 | - |
| `blockers` | `Union[list[str], str]` | 是 | `-` | 阻塞依赖（列表或 JSON 字符串） | - |
| `priority` | `int` | 否 | `3` | 优先级 1-5 | ≥ 1; ≤ 5 |

### `CheckoutTaskRequest`

任务签出请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` | 任务 ID | - |
| `agent_id` | `str` | 是 | `-` | 代理 ID | - |
| `agent_mode` | `str` | 否 | `'single'` | 代理模式 | - |
| `priority` | `int` | 否 | `3` | 优先级 1-5 | ≥ 1; ≤ 5 |
| `required_gpu_memory` | `float` | 否 | `0.0` | 所需 GPU 显存 | - |
| `timeout_hours` | `float` | 否 | `4.0` | 超时小时数 | ≥ 0 |

### `HeartbeatRequest`

任务心跳请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `agent_id` | `str` | 是 | `-` | 代理 ID | - |

### `CompleteTaskRequest`

任务完成请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `agent_id` | `str` | 是 | `-` | 代理 ID | - |

### `FailTaskRequest`

任务失败上报请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `agent_id` | `str` | 是 | `-` | 代理 ID | - |
| `reason` | `str` | 否 | `''` | 失败原因 | - |

### `AbandonTaskRequest`

任务放弃请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `agent_id` | `str` | 是 | `-` | 代理 ID | - |

### `EnqueueCheckoutRequest`

签出队列入队请求模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` | 任务 ID | - |
| `agent_id` | `str` | 是 | `-` | 代理 ID | - |
| `priority` | `int` | 否 | `3` | 优先级 1-5 | ≥ 1; ≤ 5 |
| `agent_mode` | `str` | 否 | `'single'` | 代理模式 | - |
| `required_gpu_memory` | `float` | 否 | `0.0` | 所需 GPU 显存 | - |
| `timeout_hours` | `float` | 否 | `4.0` | 超时小时数 | ≥ 0 |

### `CreateExperimentRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` | Experiment name | - |
| `control_branch` | `str` | 是 | `-` | Control branch ID | - |
| `candidate_branch` | `str` | 是 | `-` | Candidate branch ID | - |
| `traffic_split` | `float` | 否 | `0.1` | Traffic split for candidate (0.0-1.0) | - |

### `RecordExecutionRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `experiment_id` | `str` | 是 | `-` | Experiment ID | - |
| `branch` | `str` | 是 | `-` | Branch used (control/candidate) | - |
| `execution_time` | `float` | 是 | `-` | Execution time in seconds | - |
| `resource_cost` | `float` | 否 | `0.0` | Resource cost | - |

### `AssignBranchRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `project_id` | `str` | 是 | `-` | Project ID | - |

### `CreateBranchRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `base_branch` | `str | None` | 否 | `None` |  | - |
| `data` | `dict[str, Any]` | 是 | `-` |  | - |
| `metadata` | `dict[str, Any]` | 是 | `-` |  | - |

### `MergeBranchRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `source_id` | `str` | 是 | `-` |  | - |
| `target_id` | `str` | 是 | `-` |  | - |
| `strategy` | `str` | 否 | `'overwrite'` |  | - |

### `UpdateBranchRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `data` | `dict[str, Any]` | 是 | `-` |  | - |

### `MetricsUpdateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `metrics` | `dict[str, Any]` | 是 | `-` | Metrics data | - |

### `CreateSuggestionRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `trigger_type` | `str` | 是 | `-` | Trigger type | - |
| `evidence` | `dict[str, Any]` | 是 | `-` | Evidence data | - |
| `proposed_change` | `dict[str, Any]` | 是 | `-` | Proposed change | - |

### `ApplySuggestionRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `suggestion_id` | `str` | 是 | `-` | Suggestion ID | - |
| `branch_id` | `str` | 是 | `-` | Target branch ID | - |

### `PublishRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `branch_id` | `str` | 是 | `-` | Branch ID to publish | - |
| `name` | `str` | 是 | `-` | Template name | - |
| `category` | `str` | 否 | `'general'` | Template category | - |
| `description` | `str` | 否 | `''` | Template description | - |

### `SubscribeRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `category` | `str` | 是 | `-` | Category to subscribe | - |
| `project_id` | `str` | 是 | `-` | Project ID | - |

### `ExportRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `branch_id` | `str` | 是 | `-` | Branch ID to export | - |
| `include_history` | `bool` | 否 | `True` | Include evolution history | - |

### `ImportRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `template_data` | `dict[str, Any]` | 是 | `-` | Template data to import | - |
| `target_branch` | `str | None` | 否 | `None` | Target branch name | - |
| `adapt_params` | `bool` | 否 | `True` | Auto-adapt parameters | - |

### `CreateNotificationRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `project_id` | `str` | 是 | `-` | Project ID | - |
| `suggestion` | `dict[str, Any]` | 是 | `-` | Suggestion data | - |
| `priority` | `str` | 否 | `'optional'` | Priority: optional/recommended/critical | - |

### `ScanUpdatesRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `project_id` | `str` | 是 | `-` | Project ID | - |
| `suggestions` | `list[dict[str, Any]]` | 是 | `-` | List of suggestions to check | - |

### `ToolCreate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `code` | `str` | 是 | `-` | 刀具编码 (T01, T02, ...) | 最小长度: 1; 最大长度: 32 |
| `name` | `str` | 是 | `-` | 刀具名称 | 最小长度: 1; 最大长度: 128 |
| `type` | `str` | 是 | `-` | 刀具类型: end_mill/ball_mill/drill/reamer/tap/insert/grooving/threading | 最大长度: 32 |
| `diameter` | `float` | 是 | `-` | 刀具直径 (mm) | > 0 |
| `length` | `float | None` | 否 | `None` | 刀具长度 (mm) | > 0 |
| `flute_count` | `int | None` | 否 | `2` | 刃数 | ≥ 1 |
| `material` | `str | None` | 否 | `None` | 刀具材料: carbide/hss/ceramic/cbn/diamond | 最大长度: 32 |
| `coating` | `str | None` | 否 | `None` | 涂层类型: TiN/TiAlN/AlCrN/DLC/None | 最大长度: 32 |
| `max_rpm` | `float | None` | 否 | `None` | 最大允许转速 (RPM) | > 0 |
| `max_feed` | `float | None` | 否 | `None` | 最大允许进给 (mm/min) | > 0 |
| `vendor` | `str | None` | 否 | `None` | 供应商 | 最大长度: 128 |
| `cost` | `float | None` | 否 | `None` | 采购成本 | ≥ 0 |
| `notes` | `str | None` | 否 | `None` | 备注 | - |

### `ToolUpdate`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `code` | `str | None` | 否 | `None` | 刀具编码 | 最大长度: 32 |
| `name` | `str | None` | 否 | `None` | 刀具名称 | 最大长度: 128 |
| `type` | `str | None` | 否 | `None` | 刀具类型 | 最大长度: 32 |
| `diameter` | `float | None` | 否 | `None` | 刀具直径 (mm) | > 0 |
| `length` | `float | None` | 否 | `None` | 刀具长度 (mm) | > 0 |
| `flute_count` | `int | None` | 否 | `None` | 刃数 | ≥ 1 |
| `material` | `str | None` | 否 | `None` | 刀具材料 | 最大长度: 32 |
| `coating` | `str | None` | 否 | `None` | 涂层类型 | 最大长度: 32 |
| `max_rpm` | `float | None` | 否 | `None` | 最大允许转速 (RPM) | > 0 |
| `max_feed` | `float | None` | 否 | `None` | 最大允许进给 (mm/min) | > 0 |
| `usage_time` | `float | None` | 否 | `None` | 累计使用时间 (分钟) | ≥ 0 |
| `wear_amount` | `float | None` | 否 | `None` | 磨损量 (mm) | ≥ 0 |
| `status` | `str | None` | 否 | `None` | 刀具状态: active/worn/broken/maintenance | 最大长度: 16 |
| `vendor` | `str | None` | 否 | `None` | 供应商 | 最大长度: 128 |
| `cost` | `float | None` | 否 | `None` | 采购成本 | ≥ 0 |
| `notes` | `str | None` | 否 | `None` | 备注 | - |

### `ToolWearUpdate`

刀具磨损更新请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `additional_usage_time` | `float` | 否 | `0.0` | 新增使用时间 (分钟) | ≥ 0 |
| `additional_wear` | `float` | 否 | `0.0` | 新增磨损量 (mm) | ≥ 0 |
| `sharpened` | `bool` | 否 | `False` | 是否进行了刃磨 | - |

### `WearPredictRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `cutting_speed` | `float` | 否 | `150.0` | 切削速度 (m/min) | - |
| `feed_rate` | `float` | 否 | `0.2` | 进给量 (mm/rev) | - |
| `depth_of_cut` | `float` | 否 | `1.5` | 切削深度 (mm) | - |
| `material_type` | `str` | 否 | `'steel_45'` | 材料类型 | - |
| `tool_type` | `str` | 否 | `'carbide'` | 刀具类型 | - |
| `current_wear` | `float` | 否 | `0.0` | 当前磨损量 (mm) | - |
| `time_step` | `float` | 否 | `1.0` | 时间步长 (min) | - |
| `max_time` | `float` | 否 | `300.0` | 最大预测时间 (min) | - |

### `RemainingLifeRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `current_wear` | `float` | 否 | `0.1` | 当前磨损量 (mm) | - |
| `cutting_speed` | `float` | 否 | `150.0` | 切削速度 (m/min) | - |
| `feed_rate` | `float` | 否 | `0.2` | 进给量 (mm/rev) | - |
| `depth_of_cut` | `float` | 否 | `1.5` | 切削深度 (mm) | - |
| `material_type` | `str` | 否 | `'steel_45'` | 材料类型 | - |
| `tool_type` | `str` | 否 | `'carbide'` | 刀具类型 | - |

### `SuggestRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `current_wear` | `float` | 否 | `0.15` | 当前磨损量 (mm) | - |
| `remaining_life` | `float` | 否 | `50.0` | 剩余寿命 (min) | - |
| `cutting_speed` | `float` | 否 | `150.0` | 切削速度 (m/min) | - |
| `feed_rate` | `float` | 否 | `0.2` | 进给量 (mm/rev) | - |
| `depth_of_cut` | `float` | 否 | `1.5` | 切削深度 (mm) | - |
| `coolant_flow` | `float` | 否 | `10.0` | 冷却液流量 (L/min) | - |
| `material_type` | `str` | 否 | `'steel_45'` | 材料类型 | - |
| `tool_type` | `str` | 否 | `'carbide'` | 刀具类型 | - |

### `CalibrateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `measured_wear` | `float` | 否 | `0.1` | 实测磨损量 (mm) | - |
| `elapsed_time` | `float` | 否 | `30.0` | 已加工时间 (min) | - |
| `cutting_speed` | `float` | 否 | `150.0` | 切削速度 (m/min) | - |
| `feed_rate` | `float` | 否 | `0.2` | 进给量 (mm/rev) | - |
| `depth_of_cut` | `float` | 否 | `1.5` | 切削深度 (mm) | - |
| `material_type` | `str` | 否 | `'steel_45'` | 材料类型 | - |
| `tool_type` | `str` | 否 | `'carbide'` | 刀具类型 | - |

### `RealTimeCalibrateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `real_time_wear` | `float` | 否 | `0.12` | 实时磨损量 (mm) | - |
| `elapsed_time` | `float` | 否 | `30.0` | 已加工时间 (min) | - |
| `sensor_features` | `dict[str, float]` | 是 | `-` | 传感器特征（vibration_rms, cutting_force, temperature, acoustic_emission） | - |
| `cutting_speed` | `float` | 否 | `150.0` | 切削速度 (m/min) | - |
| `feed_rate` | `float` | 否 | `0.2` | 进给量 (mm/rev) | - |
| `depth_of_cut` | `float` | 否 | `1.5` | 切削深度 (mm) | - |
| `material_type` | `str` | 否 | `'steel_45'` | 材料类型 | - |
| `tool_type` | `str` | 否 | `'carbide'` | 刀具类型 | - |

### `CompensationRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `current_wear` | `float` | 否 | `0.15` | 当前磨损量 (mm) | - |
| `cutting_speed` | `float` | 否 | `150.0` | 切削速度 (m/min) | - |
| `feed_rate` | `float` | 否 | `0.2` | 进给量 (mm/rev) | - |
| `depth_of_cut` | `float` | 否 | `1.5` | 切削深度 (mm) | - |
| `material_type` | `str` | 否 | `'steel_45'` | 材料类型 | - |
| `tool_type` | `str` | 否 | `'carbide'` | 刀具类型 | - |
| `tool_diameter` | `float` | 否 | `10.0` | 刀具直径 (mm) | - |
| `machine_capabilities` | `dict[str, float] | None` | 否 | `None` | 机床能力限制（可选） | - |

### `ArtifactModel`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `type` | `str` | 是 | `-` |  | - |
| `uri` | `str` | 是 | `-` |  | - |
| `metadata` | `dict[str, Any]` | 是 | `-` |  | - |

### `WorkflowNodeModel`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `node_id` | `str` | 是 | `-` |  | - |
| `task_type` | `str` | 是 | `-` |  | - |
| `params` | `dict[str, Any]` | 是 | `-` |  | - |
| `inputs` | `dict[str, str]` | 是 | `-` |  | - |
| `retry` | `int` | 否 | `0` |  | - |
| `timeout_seconds` | `int` | 否 | `3600` |  | - |

### `WorkflowEdgeModel`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `upstream` | `str` | 是 | `-` |  | - |
| `downstream` | `str` | 是 | `-` |  | - |

### `WorkflowSpecModel`

WorkflowSpec 的 API 入参模型。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `version` | `str` | 否 | `'1.0.0'` |  | - |
| `nodes` | `list[WorkflowNodeModel]` | 是 | `-` |  | - |
| `edges` | `list[WorkflowEdgeModel]` | 是 | `-` |  | - |
| `inputs` | `dict[str, ArtifactModel]` | 是 | `-` |  | - |
| `outputs` | `dict[str, str]` | 是 | `-` |  | - |
| `metadata` | `dict[str, Any]` | 是 | `-` |  | - |

### `RunRequestModel`

提交工作流请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `spec` | `WorkflowSpecModel` | 是 | `-` |  | - |
| `inputs` | `dict[str, ArtifactModel] | None` | 否 | `None` |  | - |
| `owner_id` | `str | None` | 否 | `None` |  | - |

### `ResumeRequestModel`

断点续跑请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `spec` | `WorkflowSpecModel` | 是 | `-` |  | - |
| `inputs` | `dict[str, ArtifactModel] | None` | 否 | `None` |  | - |
| `owner_id` | `str | None` | 否 | `None` |  | - |

### `WorkflowSpecModel`

WorkflowSpec 的 API 入参（与 workflows.py 对齐，但简化为 dict 投影）.

模板市场的 spec 字段允许任意结构，由 WorkflowTemplateManifest 校验
必须包含 nodes。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `version` | `str` | 否 | `'1.0.0'` |  | - |
| `nodes` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `edges` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `inputs` | `dict[str, Any]` | 是 | `-` |  | - |
| `outputs` | `dict[str, str]` | 是 | `-` |  | - |
| `metadata` | `dict[str, Any]` | 是 | `-` |  | - |

### `PublishRequestModel`

发布工作流模板请求体.

template_dict 必须满足 workflow_template.yaml 的 schema（见
workflow_template_loader.validate_template_dict），含 id / name /
version / description / author / license / spec 等字段。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `template_dict` | `dict[str, Any]` | 是 | `-` | 模板 manifest 字典（template.yaml 的反序列化形式） | - |
| `changelog` | `str` | 否 | `''` | 版本变更说明 | - |

### `RateRequestModel`

评分请求体.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `rating` | `float` | 是 | `-` | 评分（1.0-5.0） | ≥ 1.0; ≤ 5.0 |

### `WorldModelPredictRequest`

世界模型预测请求体.

与 ``app.contracts.world_model.WorldModelPredictRequest`` 对齐，
但使用 Pydantic 以获得自动校验和 OpenAPI 文档。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `current_state` | `dict[str, float]` | 是 | `-` | 当前加工状态（字段名见 StateField，至少包含全部 8 个状态字段）。融合模式下可为空（由 unified_state 提供状态信息） | - |
| `candidate_action` | `dict[str, float]` | 是 | `-` | 候选切削参数调整量（字段名见 ActionField，4 个 delta 字段） | - |
| `horizon` | `int` | 是 | `-` |  | - |
| `model_uri` | `str` | 否 | `'model://world_model/1.0.0'` | 世界模型 URI | 最小长度: 1; 最大长度: 256 |
| `unified_state` | `dict[str, Any] | None` | 否 | `None` | ADR-020 思路 1 融合模式可选输入。包含几何特征（ADR-007）与动力学状态（ADR-013）的统一状态字典。提供时走融合路径（GeometryEncoder/DynamicsEncoder/FusionLayer）。为 None 时走原始 state_dim 字段拼接路径（向后兼容）。需配合环境变量 WORLD_MODEL_USE_FUSION=true 使用 | - |

### `TaskCreateRequest`

创建 CAM 校验任务请求体。

输入是阶段 6 G 代码报告 JSON 路径 + G 代码文件路径
+ 控制器类型 + 材料名称 + 安全 Z + 毛坯顶面 Z + CAM 后端。

若 source_gcode_generation_task_id 存在且上游任务已 SUCCEEDED，
本模块会自动从上游任务读取对应路径 + 上下文，调用方可不显式提供这些字段。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `source_gcode_generation_task_id` | `str | None` | 否 | `None` | 上游 gcode_generation 任务 ID（可选） | - |
| `source_gcode_report_path` | `str | None` | 否 | `None` | 阶段 6 G 代码报告 JSON 路径 | - |
| `source_gcode_file_path` | `str | None` | 否 | `None` | 阶段 6 生成的 G 代码文件路径 | - |
| `controller_type` | `str` | 否 | `'fanuc'` | 控制器类型（fanuc / siemens / heidenhain / haas / okuma / mazak / ...） | - |
| `material_name` | `str | None` | 否 | `None` | 材料名称（默认从上游 ChatterReport 推断） | - |
| `safety_z_mm` | `float | None` | 否 | `None` | 安全 Z 平面高度（默认从上游 G 代码报告推断） | - |
| `stock_top_z_mm` | `float | None` | 否 | `None` | 毛坯顶面 Z 高度（默认从上游 G 代码报告推断） | - |
| `cam_backend` | `str | None` | 否 | `None` | CAM 后端名称（默认自动检测或使用 PyCAM） | - |

### `TaskCreateResponse`

任务创建成功响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `cam_backend` | `str` | 是 | `-` |  | - |
| `cam_backend_version` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `pending_calibration` | `bool` | 是 | `-` |  | - |
| `disclaimer` | `dict[str, str]` | 是 | `-` |  | - |

### `TaskStatusResponse`

任务状态查询响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `cam_backend` | `str` | 是 | `-` |  | - |
| `cam_backend_version` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `pending_calibration` | `bool` | 是 | `-` |  | - |
| `validation_progress` | `dict[str, int]` | 是 | `-` |  | - |
| `validation_summary` | `dict[str, int]` | 是 | `-` |  | - |
| `disclaimer` | `dict[str, str]` | 是 | `-` |  | - |
| `source_gcode_report_path` | `str` | 否 | `''` |  | - |
| `source_gcode_file_path` | `str` | 否 | `''` |  | - |
| `prediction_method` | `str` | 否 | `'analytical'` |  | - |
| `num_features` | `int` | 否 | `0` |  | - |

### `FeatureValidationResultResponse`

单个特征校验结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `gcode_segment_ids` | `list[str]` | 是 | `-` |  | - |
| `nc_file` | `list[str]` | 是 | `-` |  | - |
| `internal_error_info` | `dict[str, Any] | None` | 否 | `None` |  | - |
| `out_of_gouge` | `bool` | 否 | `True` |  | - |
| `gouge_details` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `out_of_collision` | `bool` | 否 | `True` |  | - |
| `collision_details` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `out_of_travel` | `bool` | 否 | `True` |  | - |
| `travel_details` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `feed_rate_ok` | `bool` | 否 | `True` |  | - |
| `feed_rate_details` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `safety_z_ok` | `bool` | 否 | `True` |  | - |
| `safety_z_details` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `review_status` | `str` | 否 | `'pending'` |  | - |
| `corrected_params` | `dict[str, Any] | None` | 否 | `None` |  | - |
| `review_notes` | `str` | 否 | `''` |  | - |
| `reviewer` | `str` | 否 | `''` |  | - |

### `TaskResultResponse`

任务校验结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `results` | `list[FeatureValidationResultResponse]` | 是 | `-` |  | - |
| `total` | `int` | 是 | `-` |  | - |
| `pass_count` | `int` | 否 | `0` |  | - |
| `fail_count` | `int` | 否 | `0` |  | - |
| `warning_count` | `int` | 否 | `0` |  | - |
| `error_count` | `int` | 否 | `0` |  | - |

### `ReviewRequest`

审核请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `review_status` | `str` | 是 | `-` | 审核结果：confirmed / rejected / edited / reviewed | - |
| `corrected_params` | `dict[str, Any] | None` | 否 | `None` | 修正后的参数（review_status=edited 时需提供） | - |
| `notes` | `str` | 否 | `''` | 审核批注 | - |

### `ReviewResponse`

审核结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `corrected_params` | `dict[str, Any] | None` | 否 | `None` |  | - |
| `message` | `str` | 是 | `-` |  | - |

### `ConfirmTaskResponse`

任务确认响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `cam_report_path` | `str | None` | 否 | `None` |  | - |
| `internal_report_path` | `str | None` | 否 | `None` |  | - |
| `message` | `str` | 是 | `-` |  | - |

### `TaskCreateRequest`

创建 CAM 校验任务请求体。

输入是阶段 6 G 代码报告 JSON 路径 + G 代码文件路径
+ 控制器类型 + 材料名称 + 安全 Z + 毛坯顶面 Z + CAM 后端。

若 source_gcode_generation_task_id 存在且上游任务已 SUCCEEDED，
本模块会自动从上游任务读取对应路径 + 上下文，调用方可不显式提供这些字段。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `source_gcode_generation_task_id` | `str` | 否 | `''` | 阶段 6 gcode_generation 任务 ID（用于追溯 G 代码报告 + 文件路径 + 控制器 / 材料 / safe_z / stock_top_z）。为空时必须显式提供 gcode_report_path。 | - |
| `gcode_report_path` | `str` | 否 | `''` | 阶段 6 输出的 G 代码审核记录 JSON 路径。为空时自动从 source_gcode_generation_task_id 任务中读取。通常位于 output/gcode_generation/{gcode_task_id}/{gcode_task_id}_report.json。 | - |
| `gcode_file_path` | `str` | 否 | `''` | 阶段 6 输出的 G 代码文件路径。为空时自动从上游任务读取，或从 gcode_report_path 的 gcode_file_path 字段读取。 | - |
| `controller_type` | `str` | 否 | `'fanuc_0i'` | 目标 CNC 控制器类型：fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100。仅用于 disclaimer 显示，不影响校验逻辑。 | - |
| `material_name` | `str` | 否 | `'45#钢'` | 材料名称（用于 disclaimer 显示 + 校准状态判断）。HRC52 触发 pending_calibration 标注（继承自阶段 5/6）。 | - |
| `safe_z` | `float` | 否 | `80.0` | 安全 Z 高度 (mm)，CollisionDetector 用于碰撞检测。 | - |
| `stock_top_z` | `float` | 否 | `50.0` | 毛坯顶面 Z (mm)，CollisionDetector 用于 AABB 包围盒计算。 | - |
| `cam_backend` | `str` | 否 | `'internal_only'` | CAM 后端：internal_only（仅内部预校验）/ pycam / nx_open / powermill / manual。CAM 软件不可用时自动降级到 manual。 | - |

### `TaskCreateResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_gcode_report_path` | `str` | 是 | `-` |  | - |
| `source_gcode_file_path` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `safe_z` | `float` | 是 | `-` |  | - |
| `stock_top_z` | `float` | 是 | `-` |  | - |
| `cam_backend_requested` | `str` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `cam_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskStatusResponse`

任务状态响应（含审核进度 + 校验统计）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_gcode_report_path` | `str` | 是 | `-` |  | - |
| `source_gcode_file_path` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `safe_z` | `float` | 是 | `-` |  | - |
| `stock_top_z` | `float` | 是 | `-` |  | - |
| `gcode_total_lines` | `int` | 是 | `-` |  | - |
| `total_features` | `int` | 是 | `-` |  | - |
| `passed_features` | `int` | 是 | `-` |  | - |
| `failed_features` | `int` | 是 | `-` |  | - |
| `pending_calibration` | `bool` | 是 | `-` |  | - |
| `prediction_method` | `str` | 是 | `-` |  | - |
| `cam_backend_requested` | `str` | 是 | `-` |  | - |
| `cam_backend_used` | `str` | 是 | `-` |  | - |
| `cam_backend_fallback_reason` | `str` | 是 | `-` |  | - |
| `pending_review_count` | `int` | 是 | `-` |  | - |
| `confirmed_count` | `int` | 是 | `-` |  | - |
| `rejected_count` | `int` | 是 | `-` |  | - |
| `edited_count` | `int` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `cam_report_path` | `str` | 是 | `-` |  | - |
| `internal_report_path` | `str` | 是 | `-` |  | - |
| `error_message` | `str` | 是 | `-` |  | - |
| `started_at` | `float` | 是 | `-` |  | - |
| `completed_at` | `float` | 是 | `-` |  | - |
| `reviewed_by` | `str` | 是 | `-` |  | - |
| `reviewed_at` | `float` | 是 | `-` |  | - |
| `warnings` | `list[str]` | 是 | `-` |  | - |
| `errors` | `list[str]` | 是 | `-` |  | - |
| `cam_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `FeatureValidationResultResponse`

单条 CAM 校验结果的响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `line_range` | `list[int]` | 是 | `-` |  | - |
| `internal_check_passed` | `bool` | 是 | `-` |  | - |
| `internal_events` | `list[dict[str, Any]]` | 是 | `-` |  | - |
| `cam_check_passed` | `bool` | 是 | `-` |  | - |
| `cam_messages` | `list[str]` | 是 | `-` |  | - |
| `cam_backend_used` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `edited_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `spindle_rpm` | `float` | 是 | `-` |  | - |
| `axial_depth_mm` | `float` | 是 | `-` |  | - |
| `limit_depth_mm` | `float` | 是 | `-` |  | - |
| `stable` | `bool` | 是 | `-` |  | - |
| `safety_margin_ratio` | `float` | 是 | `-` |  | - |
| `warning` | `str` | 是 | `-` |  | - |

### `TaskResultResponse`

CAM 校验任务结果摘要（含全部特征校验结果列表）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `gcode_total_lines` | `int` | 是 | `-` |  | - |
| `total_features` | `int` | 是 | `-` |  | - |
| `passed_features` | `int` | 是 | `-` |  | - |
| `failed_features` | `int` | 是 | `-` |  | - |
| `pending_calibration` | `bool` | 是 | `-` |  | - |
| `prediction_method` | `str` | 是 | `-` |  | - |
| `cam_backend_requested` | `str` | 是 | `-` |  | - |
| `cam_backend_used` | `str` | 是 | `-` |  | - |
| `cam_backend_fallback_reason` | `str` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `cam_report_path` | `str | None` | 是 | `-` |  | - |
| `internal_report_path` | `str | None` | 是 | `-` |  | - |
| `error_message` | `str | None` | 是 | `-` |  | - |
| `feature_results` | `list[FeatureValidationResultResponse]` | 是 | `-` |  | - |
| `cam_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ReviewRequest`

工程师审核请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `action` | `str` | 是 | `-` | 审核动作：confirmed（确认特征校验结论）/ rejected（拒绝该特征，需阶段 6 重新生成 G 代码）/ edited（编辑校验参数，需同时提供 edited_params） | - |
| `edited_params` | `dict[str, Any] | None` | 否 | `None` | 工程师编辑后的参数。仅 action=edited 时必须提供。字段可为 safe_z / cam_backend / stock_top_z 的子集。edited 仅记录修改意图，不触发流水线重新执行。 | - |
| `engineer_notes` | `str` | 否 | `''` | 工程师备注（可选，便于审计追溯）。 | - |
| `reviewed_by` | `str` | 否 | `'engineer'` | 审核人标识。 | - |

### `ReviewResponse`

审核结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `edited_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `all_reviewed` | `bool` | 是 | `-` |  | - |
| `task_status` | `str` | 是 | `-` |  | - |
| `cam_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ConfirmTaskResponse`

确认任务响应（导出 cam_report + internal_report JSON，状态置为 SUCCEEDED）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `total_features` | `int` | 是 | `-` |  | - |
| `passed_features` | `int` | 是 | `-` |  | - |
| `failed_features` | `int` | 是 | `-` |  | - |
| `cam_backend_used` | `str` | 是 | `-` |  | - |
| `cam_report_path` | `str` | 是 | `-` |  | - |
| `internal_report_path` | `str` | 是 | `-` |  | - |
| `report_download_url` | `str` | 是 | `-` |  | - |
| `internal_report_download_url` | `str` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `cam_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskCreateRequest`

创建颤振预测任务请求体。

输入是阶段 4 任务 ID（追溯用）+ ChatterParams JSON 路径 + 材料 ID。
若 source_cutting_parameters_task_id 存在且上游任务已 SUCCEEDED，
本模块会自动从上游任务读取 chatter_params_path / material_id / mesh_calibrated，
调用方可不显式提供这些字段。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `source_cutting_parameters_task_id` | `str` | 是 | `-` | 阶段 4 cutting_parameters 任务 ID（用于追溯 ChatterParams 来源 及查询上游 mesh_calibrated / material_id 状态）。若上游任务不存在或未完成，必须显式提供 chatter_params_path + material_id。 | - |
| `chatter_params_path` | `str` | 否 | `''` | 阶段 4 输出的 ChatterParams JSON 路径。为空时自动从 source_cutting_parameters_task_id 任务中读取。通常位于 output/cutting_parameters/{cp_task_id}/{cp_task_id}_chatter_params.json。 | - |
| `material_id` | `str` | 否 | `''` | 材料 ID：al_6061 / ti_tc4 / steel_hrc52 等。为空时自动从阶段 4 任务中读取。HRC52 触发 pending_calibration 强制降低置信度。 | - |
| `precision_tier` | `str` | 否 | `'standard'` | 精度档位（继承自阶段 1/2/3/4）：coarse / standard / high。 | - |
| `mesh_calibrated` | `bool | None` | 否 | `None` | 上游 mesh 是否已做尺度归一化。None 时通过 source_cutting_parameters_task_id 自动查询阶段 4 任务。 | - |
| `machine_type` | `str` | 否 | `'vmc_850'` | 机床类型标识（仅供追溯，不直接影响预测算法）。 | - |

### `TaskCreateResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_cutting_parameters_task_id` | `str` | 是 | `-` |  | - |
| `chatter_params_path` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `machine_type` | `str` | 是 | `-` |  | - |
| `ltc_model_available` | `bool` | 是 | `-` |  | - |
| `chatter_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskStatusResponse`

任务状态响应（含审核进度 + 预测方法分布）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_cutting_parameters_task_id` | `str` | 是 | `-` |  | - |
| `chatter_params_path` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `machine_type` | `str` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `predicted_count` | `int` | 是 | `-` |  | - |
| `analytical_count` | `int` | 是 | `-` |  | - |
| `neural_network_count` | `int` | 是 | `-` |  | - |
| `fallback_count` | `int` | 是 | `-` |  | - |
| `ltc_model_available` | `bool` | 是 | `-` |  | - |
| `pending_count` | `int` | 是 | `-` |  | - |
| `confirmed_count` | `int` | 是 | `-` |  | - |
| `rejected_count` | `int` | 是 | `-` |  | - |
| `edited_count` | `int` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `chatter_report_path` | `str` | 是 | `-` |  | - |
| `error_message` | `str` | 是 | `-` |  | - |
| `created_at` | `float` | 是 | `-` |  | - |
| `started_at` | `float` | 是 | `-` |  | - |
| `completed_at` | `float` | 是 | `-` |  | - |
| `chatter_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `FeatureChatterResultResponse`

单条颤振预测结果的响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `spindle_rpm` | `float` | 是 | `-` |  | - |
| `axial_depth_mm` | `float` | 是 | `-` |  | - |
| `limit_depth_mm` | `float` | 是 | `-` |  | - |
| `stable` | `bool` | 是 | `-` |  | - |
| `stability_margin` | `float` | 是 | `-` |  | - |
| `method` | `str` | 是 | `-` |  | - |
| `ltc_active` | `bool` | 是 | `-` |  | - |
| `confidence` | `float` | 是 | `-` |  | - |
| `inference_time_ms` | `float` | 是 | `-` |  | - |
| `warnings` | `list[str]` | 是 | `-` |  | - |
| `material_calibration_status` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `edited_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `reviewed_by` | `str` | 是 | `-` |  | - |
| `reviewed_at` | `float` | 是 | `-` |  | - |
| `engineer_notes` | `str` | 是 | `-` |  | - |
| `source_cutting_params_task_id` | `str` | 是 | `-` |  | - |
| `machine_id` | `str` | 是 | `-` |  | - |
| `tool_id` | `str` | 是 | `-` |  | - |
| `cutting_force_coeff` | `float` | 是 | `-` |  | - |

### `TaskResultResponse`

颤振预测任务结果摘要（含全部特征预测结果列表）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_cutting_parameters_task_id` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `predicted_count` | `int` | 是 | `-` |  | - |
| `analytical_count` | `int` | 是 | `-` |  | - |
| `neural_network_count` | `int` | 是 | `-` |  | - |
| `fallback_count` | `int` | 是 | `-` |  | - |
| `ltc_model_available` | `bool` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `chatter_report_path` | `str` | 是 | `-` |  | - |
| `error_message` | `str | None` | 是 | `-` |  | - |
| `feature_results` | `list[FeatureChatterResultResponse]` | 是 | `-` |  | - |
| `chatter_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ReviewRequest`

工程师审核请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `action` | `str` | 是 | `-` | 审核动作：confirmed（确认预测结果无误）/ rejected（拒绝该特征，不进入最终 ChatterReport）/ edited（参数需修正，需同时提供 edited_params） | - |
| `edited_params` | `dict[str, Any] | None` | 否 | `None` | 工程师编辑后的参数。仅 action=edited 时必须提供。字段可为 limit_depth_mm / axial_depth_mm / stable（0/1）的子集。 | - |
| `engineer_notes` | `str` | 否 | `''` | 工程师备注（可选，便于审计追溯）。 | - |
| `reviewed_by` | `str` | 否 | `'engineer'` | 审核人标识。 | - |

### `ReviewResponse`

审核结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `all_reviewed` | `bool` | 是 | `-` |  | - |
| `task_status` | `str` | 是 | `-` |  | - |
| `chatter_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ExportChatterReportResponse`

导出 ChatterReport 响应（阶段 6 输入）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_cutting_parameters_task_id` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `chatter_report_path` | `str` | 是 | `-` |  | - |
| `download_url` | `str` | 是 | `-` |  | - |
| `chatter_params_ready` | `bool` | 是 | `-` |  | - |
| `cutting_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskCreateRequest`

创建切削参数推荐任务请求体。

输入是阶段 3 STEP 文件路径 + 阶段 2 confirmed_features.json 路径 + 材料 ID。
STEP 文件路径仅作追溯用，本模块不重新解析 STEP，
实际特征参数取自阶段 2 confirmed_features.json。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `source_parametric_geometry_task_id` | `str` | 是 | `-` | 阶段 3 parametric_geometry 任务 ID（用于追溯 STEP 文件来源 及查询上游 mesh_calibrated 状态）。若上游任务不存在或未完成，按未标定 mesh 处理。 | - |
| `step_file_path` | `str` | 是 | `-` | 阶段 3 输出的 STEP 文件路径（仅作追溯用，本模块不解析 STEP）。通常位于 output/parametric_geometry/{pg_task_id}/{pg_task_id}_final.step。 | - |
| `input_features_path` | `str` | 是 | `-` | 阶段 2 导出的 confirmed_features.json 路径。切削参数推荐基于其中的 feature_id / feature_type 字段。 | - |
| `material_id` | `str` | 是 | `-` | 材料 ID：al_6061 / ti_tc4 / steel_hrc52 等。材料数据库中 17 种材料可用，HRC52 通过内存补充数据（待自采校准）。 | - |
| `precision_tier` | `str` | 否 | `'standard'` | 精度档位（继承自阶段 1/2/3）：coarse / standard / high。 | - |
| `mesh_calibrated` | `bool | None` | 否 | `None` | 上游 mesh 是否已做尺度归一化。None 时通过 source_parametric_geometry_task_id 自动查询阶段 3 任务。 | - |
| `machine_type` | `str` | 否 | `'vmc_850'` | 机床类型标识（仅供追溯，不直接影响推荐算法）。 | - |
| `tool_diameter_mm` | `float` | 否 | `10.0` | 刀具直径 (mm)，影响主轴转速与径向切深计算。 | - |
| `num_flutes` | `int` | 否 | `4` | 齿数，影响进给速度计算 feed_rate = spindle_rpm * num_flutes * feed_per_tooth。 | - |

### `TaskCreateResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_parametric_geometry_task_id` | `str` | 是 | `-` |  | - |
| `step_file_path` | `str` | 是 | `-` |  | - |
| `input_features_path` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `machine_type` | `str` | 是 | `-` |  | - |
| `tool_diameter_mm` | `float` | 是 | `-` |  | - |
| `num_flutes` | `int` | 是 | `-` |  | - |
| `cutting_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskStatusResponse`

任务状态响应（含审核进度）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_parametric_geometry_task_id` | `str` | 是 | `-` |  | - |
| `step_file_path` | `str` | 是 | `-` |  | - |
| `input_features_path` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `machine_type` | `str` | 是 | `-` |  | - |
| `tool_diameter_mm` | `float` | 是 | `-` |  | - |
| `num_flutes` | `int` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `recommended_count` | `int` | 是 | `-` |  | - |
| `pending_count` | `int` | 是 | `-` |  | - |
| `confirmed_count` | `int` | 是 | `-` |  | - |
| `rejected_count` | `int` | 是 | `-` |  | - |
| `edited_count` | `int` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `chatter_params_path` | `str` | 是 | `-` |  | - |
| `error_message` | `str` | 是 | `-` |  | - |
| `created_at` | `float` | 是 | `-` |  | - |
| `started_at` | `float` | 是 | `-` |  | - |
| `completed_at` | `float` | 是 | `-` |  | - |
| `cutting_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `RecommendedParamsResponse`

单条推荐切削参数的响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `operation` | `str` | 是 | `-` |  | - |
| `spindle_speed_rpm` | `float` | 是 | `-` |  | - |
| `feed_rate_mm_per_min` | `float` | 是 | `-` |  | - |
| `feed_per_tooth_mm` | `float` | 是 | `-` |  | - |
| `cutting_speed_m_per_min` | `float` | 是 | `-` |  | - |
| `axial_depth_mm` | `float` | 是 | `-` |  | - |
| `radial_depth_mm` | `float` | 是 | `-` |  | - |
| `estimated_cutting_time_s` | `float` | 是 | `-` |  | - |
| `tool_life_estimate_min` | `float` | 是 | `-` |  | - |
| `warnings` | `list[str]` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `edited_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `reviewed_by` | `str` | 是 | `-` |  | - |
| `reviewed_at` | `float` | 是 | `-` |  | - |
| `engineer_notes` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `tool_diameter_mm` | `float` | 是 | `-` |  | - |
| `num_flutes` | `int` | 是 | `-` |  | - |

### `TaskResultResponse`

切削参数任务结果摘要（含全部推荐参数列表）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_parametric_geometry_task_id` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `recommended_count` | `int` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `chatter_params_path` | `str` | 是 | `-` |  | - |
| `error_message` | `str | None` | 是 | `-` |  | - |
| `recommended_params` | `list[RecommendedParamsResponse]` | 是 | `-` |  | - |
| `cutting_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ReviewRequest`

工程师审核请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `action` | `str` | 是 | `-` | 审核动作：confirmed（确认推荐参数无误）/ rejected（拒绝该特征，不进入最终 ChatterParams）/ edited（参数需修正，需同时提供 edited_params） | - |
| `edited_params` | `dict[str, Any] | None` | 否 | `None` | 工程师编辑后的参数。仅 action=edited 时必须提供。字段可为 spindle_speed_rpm / feed_rate_mm_per_min / feed_per_tooth_mm / cutting_speed_m_per_min / axial_depth_mm / radial_depth_mm 的子集。 | - |
| `engineer_notes` | `str` | 否 | `''` | 工程师备注（可选，便于审计追溯）。 | - |
| `reviewed_by` | `str` | 否 | `'engineer'` | 审核人标识。 | - |

### `ReviewResponse`

审核结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `all_reviewed` | `bool` | 是 | `-` |  | - |
| `task_status` | `str` | 是 | `-` |  | - |
| `cutting_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ExportChatterParamsResponse`

导出 ChatterParams 响应（阶段 5 输入）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_parametric_geometry_task_id` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `chatter_params_path` | `str` | 是 | `-` |  | - |
| `download_url` | `str` | 是 | `-` |  | - |
| `chatter_params_ready` | `bool` | 是 | `-` |  | - |
| `cutting_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskCreateFromPathRequest`

通过 mesh 路径 + 关联重建任务 ID 创建特征提取任务（链路模式）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `mesh_path` | `str` | 是 | `-` | 输入 mesh 文件路径（PLY/STL/GLB/OBJ）。通常为 image_to_3d 任务的输出 mesh，或用户外部上传的 mesh。 | - |
| `source_reconstruction_task_id` | `str` | 否 | `''` | 关联的拍照重建任务 ID（可选）。若提供且上游任务已 SUCCEEDED，则系统自动查询 mesh 是否已做尺度归一化。若不提供或上游任务不存在，则视为外部上传，按未标定 mesh 处理。 | - |

### `TaskCreateResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `input_mesh_path` | `str` | 是 | `-` |  | - |
| `source_reconstruction_task_id` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `feature_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskStatusResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `input_mesh_path` | `str` | 是 | `-` |  | - |
| `source_reconstruction_task_id` | `str` | 是 | `-` |  | - |
| `vertex_count` | `int` | 是 | `-` |  | - |
| `face_count` | `int` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `plane_count` | `int` | 是 | `-` |  | - |
| `cylinder_count` | `int` | 是 | `-` |  | - |
| `hole_count` | `int` | 是 | `-` |  | - |
| `boss_count` | `int` | 是 | `-` |  | - |
| `plane_duration_seconds` | `float` | 是 | `-` |  | - |
| `cylinder_duration_seconds` | `float` | 是 | `-` |  | - |
| `hole_duration_seconds` | `float` | 是 | `-` |  | - |
| `total_duration_seconds` | `float` | 是 | `-` |  | - |
| `error_message` | `str` | 是 | `-` |  | - |
| `reviewed_by` | `str` | 是 | `-` |  | - |
| `reviewed_at` | `float` | 是 | `-` |  | - |
| `exported_features_path` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `feature_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `FeatureItemResponse`

单条特征的简化响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `params` | `dict[str, Any]` | 是 | `-` |  | - |
| `confidence` | `float` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `engineer_notes` | `str` | 是 | `-` |  | - |
| `edited` | `bool` | 是 | `-` |  | - |
| `edited_params` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskResultResponse`

特征提取结果（含完整特征列表）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `features` | `list[FeatureItemResponse]` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `plane_count` | `int` | 是 | `-` |  | - |
| `cylinder_count` | `int` | 是 | `-` |  | - |
| `hole_count` | `int` | 是 | `-` |  | - |
| `boss_count` | `int` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `feature_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ReviewRequest`

工程师审核请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `action` | `str` | 是 | `-` | 审核动作：confirmed（确认无误）/ rejected（误识别，丢弃）/ edited（参数需修正，需同时提供 edited_params） | - |
| `edited_params` | `dict[str, Any] | None` | 否 | `None` | 工程师编辑后的参数。仅 action=edited 时必须提供，字段结构需与原始 params 一致（如 plane: normal/offset/area_mm2）。 | - |
| `engineer_notes` | `str` | 否 | `''` | 工程师备注（可选，便于审计追溯）。 | - |
| `reviewed_by` | `str` | 否 | `'engineer'` | 审核人标识（默认 'engineer'，便于多工程师协同场景区分）。 | - |

### `ReviewResponse`

审核结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `all_reviewed` | `bool` | 是 | `-` |  | - |
| `task_status` | `str` | 是 | `-` |  | - |
| `feature_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ExportResponse`

导出已确认特征响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `exported_features_path` | `str` | 是 | `-` |  | - |
| `confirmed_feature_count` | `int` | 是 | `-` |  | - |
| `download_url` | `str` | 是 | `-` |  | - |
| `feature_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskCreateRequest`

创建 G 代码生成任务请求体。

输入是阶段 5 ChatterReport JSON 路径 + 阶段 3 OperationPlan JSON 路径
+ 控制器类型 + 材料名称 + 程序号 + 安全 Z + 毛坯顶面 Z。

若 source_chatter_prediction_task_id / source_parametric_geometry_task_id
存在且上游任务已 SUCCEEDED，本模块会自动从上游任务读取对应路径，
调用方可不显式提供这些字段。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `source_chatter_prediction_task_id` | `str` | 否 | `''` | 阶段 5 chatter_prediction 任务 ID（用于追溯 ChatterReport 路径）。为空时必须显式提供 chatter_report_path。 | - |
| `source_parametric_geometry_task_id` | `str` | 否 | `''` | 阶段 3 parametric_geometry 任务 ID（用于追溯 OperationPlan 路径）。为空时必须显式提供 operation_plan_path。 | - |
| `chatter_report_path` | `str` | 否 | `''` | 阶段 5 输出的 ChatterReport JSON 路径。为空时自动从 source_chatter_prediction_task_id 任务中读取。通常位于 output/chatter_prediction/{cp_task_id}/{cp_task_id}_chatter_report.json。 | - |
| `operation_plan_path` | `str` | 否 | `''` | 阶段 3 输出的 OperationPlan JSON 路径。为空时自动从 source_parametric_geometry_task_id 任务中读取。 | - |
| `controller_type` | `str` | 否 | `'fanuc_0i'` | 目标 CNC 控制器类型：fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100。决定 G 代码文件扩展名（.nc / .mpf / .h）。 | - |
| `material_name` | `str` | 否 | `'45#钢'` | 材料名称（用于 G 代码注释 + 精度告知）。为空时自动从阶段 5 任务中读取 material_id。HRC52 触发 pending_calibration 标注。 | - |
| `program_number` | `int` | 否 | `1000` | G 代码程序号（O 号，Fanuc 习惯 1-9999）。 | ≥ 1; ≤ 9999 |
| `safe_z` | `float` | 否 | `80.0` | 安全 Z 高度 (mm)，G 代码在特征切换时抬至此高度。 | - |
| `stock_top_z` | `float` | 否 | `50.0` | 毛坯顶面 Z (mm)，G 代码起刀点参考。 | - |

### `TaskCreateResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_chatter_report_path` | `str` | 是 | `-` |  | - |
| `source_operation_plan_path` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `program_number` | `int` | 是 | `-` |  | - |
| `safe_z` | `float` | 是 | `-` |  | - |
| `stock_top_z` | `float` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `gcode_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskStatusResponse`

任务状态响应（含审核进度 + 生成统计）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_chatter_report_path` | `str` | 是 | `-` |  | - |
| `source_operation_plan_path` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `program_number` | `int` | 是 | `-` |  | - |
| `safe_z` | `float` | 是 | `-` |  | - |
| `stock_top_z` | `float` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `stable_features` | `int` | 是 | `-` |  | - |
| `unstable_features` | `int` | 是 | `-` |  | - |
| `pending_calibration` | `bool` | 是 | `-` |  | - |
| `prediction_method` | `str` | 是 | `-` |  | - |
| `pending_review_count` | `int` | 是 | `-` |  | - |
| `confirmed_count` | `int` | 是 | `-` |  | - |
| `rejected_count` | `int` | 是 | `-` |  | - |
| `edited_count` | `int` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `gcode_file_path` | `str` | 是 | `-` |  | - |
| `gcode_report_path` | `str` | 是 | `-` |  | - |
| `error_message` | `str` | 是 | `-` |  | - |
| `started_at` | `float` | 是 | `-` |  | - |
| `completed_at` | `float` | 是 | `-` |  | - |
| `reviewed_by` | `str` | 是 | `-` |  | - |
| `reviewed_at` | `float` | 是 | `-` |  | - |
| `warnings` | `list[str]` | 是 | `-` |  | - |
| `errors` | `list[str]` | 是 | `-` |  | - |
| `gcode_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `FeatureGCodeResultResponse`

单条 G 代码生成结果的响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `material_id` | `str` | 是 | `-` |  | - |
| `spindle_rpm` | `float` | 是 | `-` |  | - |
| `axial_depth_mm` | `float` | 是 | `-` |  | - |
| `limit_depth_mm` | `float` | 是 | `-` |  | - |
| `stable` | `bool` | 是 | `-` |  | - |
| `safety_margin_ratio` | `float` | 是 | `-` |  | - |
| `gcode_lines` | `list[str]` | 是 | `-` |  | - |
| `line_range` | `list[int]` | 是 | `-` |  | - |
| `warning` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `edited_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskResultResponse`

G 代码生成任务结果摘要（含全部特征 G 代码段列表）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `program_number` | `int` | 是 | `-` |  | - |
| `total_features` | `int` | 是 | `-` |  | - |
| `stable_features` | `int` | 是 | `-` |  | - |
| `unstable_features` | `int` | 是 | `-` |  | - |
| `pending_calibration` | `bool` | 是 | `-` |  | - |
| `prediction_method` | `str` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `gcode_file_path` | `str | None` | 是 | `-` |  | - |
| `gcode_report_path` | `str | None` | 是 | `-` |  | - |
| `error_message` | `str | None` | 是 | `-` |  | - |
| `feature_results` | `list[FeatureGCodeResultResponse]` | 是 | `-` |  | - |
| `gcode_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ReviewRequest`

工程师审核请求体。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `action` | `str` | 是 | `-` | 审核动作：confirmed（确认 G 代码段无误）/ rejected（拒绝该特征，不进入最终 G 代码）/ edited（参数需修正，需同时提供 edited_params） | - |
| `edited_params` | `dict[str, Any] | None` | 否 | `None` | 工程师编辑后的参数。仅 action=edited 时必须提供。字段可为 axial_depth_mm / limit_depth_mm / stable（bool）的子集。edited 仅记录修改意图，不触发 G 代码重新生成。 | - |
| `engineer_notes` | `str` | 否 | `''` | 工程师备注（可选，便于审计追溯）。 | - |
| `reviewed_by` | `str` | 否 | `'engineer'` | 审核人标识。 | - |

### `ReviewResponse`

审核结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `all_reviewed` | `bool` | 是 | `-` |  | - |
| `task_status` | `str` | 是 | `-` |  | - |
| `gcode_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ConfirmTaskResponse`

确认任务响应（导出 G 代码 + 报告 JSON，状态置为 SUCCEEDED）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `controller_type` | `str` | 是 | `-` |  | - |
| `material_name` | `str` | 是 | `-` |  | - |
| `program_number` | `int` | 是 | `-` |  | - |
| `total_features` | `int` | 是 | `-` |  | - |
| `exported_features` | `int` | 是 | `-` |  | - |
| `gcode_file_path` | `str` | 是 | `-` |  | - |
| `gcode_report_path` | `str` | 是 | `-` |  | - |
| `download_url` | `str` | 是 | `-` |  | - |
| `report_download_url` | `str` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `gcode_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskCreateRequest`

创建任务时的可选元数据。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `calibration_anchor_distance` | `float | None` | 否 | `None` | 标定块在 SfM 无量纲坐标系下的距离。None 表示未提供，输出无量纲 mesh（仅可视化用）。如需得到带 mm 尺度的 mesh，请通过 GET /precision_info 了解标定块放置方法。 | - |

### `TaskCreateResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `photo_count` | `int` | 是 | `-` |  | - |
| `precision_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskStatusResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `photo_count` | `int` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `num_images_registered` | `int` | 是 | `-` |  | - |
| `calibrated` | `bool` | 是 | `-` |  | - |
| `scale_factor` | `float` | 是 | `-` |  | - |
| `colmap_duration_seconds` | `float` | 是 | `-` |  | - |
| `openmvs_duration_seconds` | `float` | 是 | `-` |  | - |
| `total_duration_seconds` | `float` | 是 | `-` |  | - |
| `error_message` | `str` | 是 | `-` |  | - |
| `precision_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `NL2CADRequest`

Request model for NL to CAD conversion.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `description` | `str` | 是 | `-` | 自然语言零件描述 | 最小长度: 1; 最大长度: 2000 |
| `output_format` | `str` | 否 | `'stl'` | 输出格式 | 正则: `^(stl|step|obj|gltf)$` |

### `NL2CADResponse`

Response model for NL to CAD conversion.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_path` | `str` | 是 | `-` | 生成的模型文件路径 | - |
| `params` | `dict[str, Any]` | 是 | `-` | 提取的CAD参数 | - |
| `confidence` | `float` | 是 | `-` | 参数提取置信度 | ≥ 0.0; ≤ 1.0 |

### `RefineRequest`

Request model for model refinement.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `current_params` | `dict[str, Any]` | 是 | `-` | 当前模型参数 | - |
| `instruction` | `str` | 是 | `-` | 微调指令 | 最小长度: 1; 最大长度: 1000 |
| `output_format` | `str` | 否 | `'stl'` | 输出格式 | 正则: `^(stl|step|obj|gltf)$` |

### `RefineResponse`

Response model for model refinement.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_path` | `str` | 是 | `-` | 更新后的模型文件路径 | - |
| `params` | `dict[str, Any]` | 是 | `-` | 更新后的CAD参数 | - |

### `ExtractParamsRequest`

Request model for parameter extraction only.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `description` | `str` | 是 | `-` | 自然语言零件描述 | 最小长度: 1; 最大长度: 2000 |

### `ExtractParamsResponse`

Response model for parameter extraction.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `params` | `dict[str, Any]` | 是 | `-` | 提取的CAD参数 | - |
| `confidence` | `float` | 是 | `-` | 置信度 | ≥ 0.0; ≤ 1.0 |

### `ProcessPlanningRequest`

Request model for process planning.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `cad_params` | `dict[str, Any]` | 是 | `-` | CAD参数 | - |
| `material` | `str` | 是 | `-` | 材料类型 | - |
| `machine_type` | `str` | 否 | `'cnc_mill'` | 机床类型 | - |
| `precision` | `str` | 否 | `'finish'` | 精度等级 | - |

### `ProcessPlanningResponse`

Response model for process planning.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `process_plan` | `dict[str, Any]` | 是 | `-` | 工艺规划结果 | - |

### `NCCodeRequest`

Request model for NC code generation.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `process_plan` | `dict[str, Any]` | 是 | `-` | 工艺规划 | - |
| `machine_type` | `str` | 否 | `'cnc_mill'` | 机床类型 | - |

### `NCCodeResponse`

Response model for NC code generation.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `nc_code` | `str` | 是 | `-` | 生成的NC代码 | - |

### `FullPipelineRequest`

Request model for full pipeline execution.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `description` | `str` | 是 | `-` | 自然语言零件描述 | 最小长度: 1; 最大长度: 2000 |
| `machine_type` | `str` | 否 | `'cnc_mill'` | 机床类型 | - |
| `material` | `str` | 否 | `'steel'` | 材料类型 | - |

### `FullPipelineResponse`

Response model for full pipeline execution.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `model_path` | `str` | 是 | `-` | 生成的模型文件路径 | - |
| `cad_params` | `dict[str, Any]` | 是 | `-` | CAD参数 | - |
| `process_plan` | `dict[str, Any]` | 是 | `-` | 工艺规划 | - |
| `nc_code` | `str` | 是 | `-` | NC代码 | - |
| `simulation_result` | `dict[str, Any]` | 是 | `-` | 仿真结果 | - |

### `TaskCreateRequest`

创建参数化几何任务请求体。

输入是阶段 2 feature_extraction 模块导出的 confirmed_features.json 路径，
而非 mesh 文件（阶段 3 不再处理 mesh，仅处理已审核的特征参数）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `source_feature_extraction_task_id` | `str` | 是 | `-` | 阶段 2 feature_extraction 任务 ID（用于追溯 confirmed_features.json 来源 及查询上游 mesh_calibrated 状态）。若上游任务不存在或未完成，按未标定 mesh 处理。 | - |
| `input_features_path` | `str` | 是 | `-` | 阶段 2 导出的 confirmed_features.json 文件路径。通常位于 output/feature_extraction/{fe_task_id}/confirmed_features_{fe_task_id}.json。 | - |
| `precision_tier` | `str` | 否 | `'standard'` | 精度档位（继承自阶段 1/2）：coarse / standard / high。本模块不引入新档位，仅用于显示告知。 | - |
| `mesh_calibrated` | `bool | None` | 否 | `None` | 上游 mesh 是否已做尺度归一化。若不提供（None），系统自动通过 source_feature_extraction_task_id 查询阶段 1 image_to_3d 任务的 calibrated 字段。若查询失败，按未标定处理。 | - |

### `TaskCreateResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_feature_extraction_task_id` | `str` | 是 | `-` |  | - |
| `input_features_path` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `step_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `TaskStatusResponse`

任务状态响应（含完整审核进度）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_feature_extraction_task_id` | `str` | 是 | `-` |  | - |
| `input_features_path` | `str` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `pending_count` | `int` | 是 | `-` |  | - |
| `confirmed_count` | `int` | 是 | `-` |  | - |
| `rejected_count` | `int` | 是 | `-` |  | - |
| `edited_count` | `int` | 是 | `-` |  | - |
| `step_output_path` | `str | None` | 是 | `-` |  | - |
| `final_step_path` | `str | None` | 是 | `-` |  | - |
| `engine_used` | `str | None` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `error_message` | `str | None` | 是 | `-` |  | - |
| `created_at` | `float` | 是 | `-` |  | - |
| `updated_at` | `float` | 是 | `-` |  | - |
| `step_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `FeatureRefResponse`

单条已审核特征引用的响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `source_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `edited_params` | `dict[str, Any] | None` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `engineer_notes` | `str | None` | 是 | `-` |  | - |
| `reviewed_by` | `str | None` | 是 | `-` |  | - |
| `reviewed_at` | `float | None` | 是 | `-` |  | - |

### `TaskResultResponse`

参数化几何任务结果摘要（含装配信息与特征列表）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_feature_extraction_task_id` | `str` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `brep_shape_count` | `int` | 是 | `-` |  | - |
| `engine_used` | `str | None` | 是 | `-` |  | - |
| `step_output_path` | `str | None` | 是 | `-` |  | - |
| `final_step_path` | `str | None` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `cam_validation_required` | `bool` | 是 | `-` |  | - |
| `error_message` | `str | None` | 是 | `-` |  | - |
| `assembly_summary` | `dict[str, Any] | None` | 是 | `-` |  | - |
| `features` | `list[FeatureRefResponse]` | 是 | `-` |  | - |
| `step_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ReviewRequest`

工程师审核请求体（第一轮：审核 STEP 中的特征表达）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `action` | `str` | 是 | `-` | 审核动作：confirmed（确认 STEP 表达正确）/ rejected（误识别，从最终 STEP 中移除）/ edited（参数需修正，需同时提供 edited_params） | - |
| `edited_params` | `dict[str, Any] | None` | 否 | `None` | 工程师编辑后的参数。仅 action=edited 时必须提供，字段结构需与 source_params 一致（如 cylinder: center/axis/radius_mm/height_mm）。 | - |
| `engineer_notes` | `str` | 否 | `''` | 工程师备注（可选，便于审计追溯）。 | - |
| `reviewed_by` | `str` | 否 | `'engineer'` | 审核人标识（默认 'engineer'，便于多工程师协同场景区分）。 | - |

### `ReviewResponse`

审核结果响应。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `feature_id` | `str` | 是 | `-` |  | - |
| `feature_type` | `str` | 是 | `-` |  | - |
| `review_status` | `str` | 是 | `-` |  | - |
| `effective_params` | `dict[str, Any]` | 是 | `-` |  | - |
| `all_reviewed` | `bool` | 是 | `-` |  | - |
| `task_status` | `str` | 是 | `-` |  | - |
| `step_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `FinalizeResponse`

最终化 STEP 响应（第二轮 STEP 生成结果）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 是 | `-` |  | - |
| `status` | `str` | 是 | `-` |  | - |
| `source_feature_extraction_task_id` | `str` | 是 | `-` |  | - |
| `feature_count` | `int` | 是 | `-` |  | - |
| `brep_shape_count` | `int` | 是 | `-` |  | - |
| `engine_used` | `str | None` | 是 | `-` |  | - |
| `step_output_path` | `str | None` | 是 | `-` |  | - |
| `final_step_path` | `str | None` | 是 | `-` |  | - |
| `precision_tier` | `str` | 是 | `-` |  | - |
| `mesh_calibrated` | `bool` | 是 | `-` |  | - |
| `assembly_summary` | `dict[str, Any] | None` | 是 | `-` |  | - |
| `download_url` | `str` | 是 | `-` |  | - |
| `step_disclaimer` | `dict[str, Any]` | 是 | `-` |  | - |

### `ExplainRequest`

模型预测结果解释请求（修复：拆分时丢失的类型定义，2026-08-03）

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `force_pred` | `float` | 否 | `0.0` | 切削力预测值 (N) | - |
| `force_conf` | `float` | 否 | `0.0` | 切削力置信度 (%) | - |
| `wear_pred` | `float` | 否 | `0.0` | 刀具磨损预测值 (mm) | - |
| `wear_conf` | `float` | 否 | `0.0` | 刀具磨损置信度 (%) | - |
| `visual_status` | `str` | 否 | `''` | 工件状态描述 | - |
| `anomaly_prob` | `float` | 否 | `0.0` | 异常概率 (%) | - |

### `SimulationRequest`

Request model for voxel cutting simulation.

Contains all parameters needed to run a machining simulation including
project identification, tool geometry, G-code toolpath, and stock model.

Attributes:
    project_id: Project identifier for associating simulation jobs.
    voxel_size: Voxel resolution in mm (0.1-10.0). Smaller = higher accuracy.
    tool_diameter: Tool diameter in mm (0.5-300.0).
    tool_length: Tool cutting length in mm (1.0-500.0).
    tool_type: Tool type - "flat" (flat end mill), "ball" (ball nose), "drill".
    tool_corner_radius: Tool corner radius in mm (0.0-150.0).
    gcode: G-code text content for toolpath parsing.
    safe_z_height: Safe plane height in mm (0.0-200.0).
    stock_stl_path: Path to stock STL file (relative or absolute).
    source_file_path: Source file path (STEP/DXF) for auto-regeneration if STL is missing.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `project_id` | `str` | 否 | `'default'` | Project ID for associating simulation jobs. | - |
| `voxel_size` | `float` | 否 | `1.0` | Voxel resolution in mm. Smaller values yield higher accuracy. | ≥ 0.1; ≤ 10.0 |
| `tool_diameter` | `float` | 否 | `10.0` | Tool diameter in mm. | ≥ 0.5; ≤ 300.0 |
| `tool_length` | `float` | 否 | `50.0` | Tool cutting length in mm. | ≥ 1.0; ≤ 500.0 |
| `tool_type` | `str` | 否 | `'flat'` | Tool type: flat (flat end mill), ball (ball nose), drill. | 正则: `^(flat|ball|drill)$` |
| `tool_corner_radius` | `float` | 否 | `0.0` | Tool corner radius in mm. | ≥ 0.0; ≤ 150.0 |
| `gcode` | `str` | 否 | `''` | G-code text content for toolpath parsing. | - |
| `safe_z_height` | `float` | 否 | `10.0` | Safe plane height in mm. | ≥ 0.0; ≤ 200.0 |
| `stock_stl_path` | `str` | 否 | `''` | Path to stock STL file (server-relative or absolute). | - |
| `source_file_path` | `str` | 否 | `''` | Source file path (STEP/DXF) for auto-regeneration when STL is missing. | - |

### `SimulationResponse`

Response model containing voxel simulation results.

Attributes:
    task_id: Unique simulation task identifier.
    stock_stl_url: URL path to the machined workpiece STL file.
    collision_collided: Whether any collision was detected.
    collision_positions: List of [x, y, z] collision coordinates.
    collision_segment_indices: Indices of toolpath segments with collisions.
    collision_severity: Collision severity level ("none"/"warning"/"critical").
    duration_seconds: Total simulation time in seconds.
    voxel_count: Total number of voxels in the stock model.
    removed_voxel_count: Number of voxels removed during simulation.
    voxel_size: Voxel resolution used (mm).
    original_bbox: Original stock bounding box dimensions.
    toolpath_segment_count: Number of toolpath segments processed.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 否 | `''` |  | - |
| `stock_stl_url` | `str` | 否 | `''` |  | - |
| `collision_collided` | `bool` | 否 | `False` |  | - |
| `collision_positions` | `list[list[float]]` | 否 | `[]` |  | - |
| `collision_segment_indices` | `list[int]` | 否 | `[]` |  | - |
| `collision_severity` | `str` | 否 | `'none'` |  | - |
| `duration_seconds` | `float` | 否 | `0.0` |  | - |
| `voxel_count` | `int` | 否 | `0` |  | - |
| `removed_voxel_count` | `int` | 否 | `0` |  | - |
| `voxel_size` | `float` | 否 | `1.0` |  | - |
| `original_bbox` | `dict[str, float] | None` | 否 | `None` |  | - |
| `toolpath_segment_count` | `int` | 否 | `0` |  | - |

### `SimulationStatusResponse`

Response model for simulation task status queries.

Attributes:
    task_id: The simulation task identifier.
    status: Current task status ("pending"/"running"/"completed").
    progress: Task completion progress (0.0-1.0).
    result: Simulation result data, available only when completed.

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `task_id` | `str` | 否 | `''` |  | - |
| `status` | `str` | 否 | `'pending'` |  | - |
| `progress` | `float` | 否 | `0.0` |  | - |
| `result` | `SimulationResponse | None` | 否 | `None` |  | - |

### `ConflictCheckRequest`

Request model for tool-slot compatibility check.

Attributes:
    tool_diameter: Tool diameter in mm (0.5-300.0).
    slot_width: Slot width in mm (0.1-500.0).
    material: Workpiece material (e.g., "45 steel").
    operation: Machining operation type (e.g., "slot milling").

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `tool_diameter` | `float` | 否 | `20.0` | Tool diameter in mm. | ≥ 0.5; ≤ 300.0 |
| `slot_width` | `float` | 否 | `10.0` | Slot width in mm. | ≥ 0.1; ≤ 500.0 |
| `material` | `str` | 否 | `'45 steel'` | Workpiece material. | - |
| `operation` | `str` | 否 | `'slot milling'` | Machining operation type. | - |

### `ExportAnimationRequest`

Request model for simulation animation export.

Attributes:
    nc_code: G-code text content for toolpath visualization.
    format: Output format - "gif" or "mp4".
    voxel_size: Voxel resolution in mm (0.1-10.0).
    tool_diameter: Tool diameter in mm (0.5-300.0).
    tool_length: Tool cutting length in mm (1.0-500.0).
    tool_type: Tool type - "flat", "ball", "drill".

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `nc_code` | `str` | 否 | `''` | G-code text content for toolpath visualization. | - |
| `format` | `str` | 否 | `'gif'` | Output format: gif or mp4. | 正则: `^(gif|mp4)$` |
| `voxel_size` | `float` | 否 | `1.0` | Voxel resolution in mm. | ≥ 0.1; ≤ 10.0 |
| `tool_diameter` | `float` | 否 | `10.0` | Tool diameter in mm. | ≥ 0.5; ≤ 300.0 |
| `tool_length` | `float` | 否 | `50.0` | Tool cutting length in mm. | ≥ 1.0; ≤ 500.0 |
| `tool_type` | `str` | 否 | `'flat'` | Tool type. | 正则: `^(flat|ball|drill)$` |

### `AutoDiffCompareRequest`

Auto-Diff 几何比对请求。

Attributes:
    design_stl_path: 设计模型（目标工件）STL 路径，须位于允许目录内。
    actual_stl_path: 仿真切削结果 STL 路径（VoxelCutter 输出）。
    voxel_size: 体素分辨率（mm），默认 0.5，越小越精确但越慢。
    export_diff_stl: 是否导出偏差可视化 STL，默认 True。
    gouge_warn_ratio: 过切告警阈值（体积占比），可选覆盖默认值。
    gouge_reject_ratio: 过切拒收阈值（体积占比），可选覆盖默认值。
    leftover_warn_ratio: 残料告警阈值（体积占比），可选覆盖默认值。
    leftover_reject_ratio: 残料拒收阈值（体积占比），可选覆盖默认值。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `design_stl_path` | `str` | 是 | `-` | 设计模型 STL 路径（须位于允许目录内）。 | - |
| `actual_stl_path` | `str` | 是 | `-` | 仿真结果 STL 路径（须位于允许目录内）。 | - |
| `voxel_size` | `float` | 否 | `0.5` | 体素分辨率（mm），越小越精确但越慢。 | ≥ 0.1; ≤ 5.0 |
| `export_diff_stl` | `bool` | 否 | `True` | 是否导出偏差可视化 STL。 | - |
| `gouge_warn_ratio` | `float | None` | 否 | `None` | 过切告警阈值（体积占比），留空使用默认 0.0001。 | ≥ 0.0; ≤ 1.0 |
| `gouge_reject_ratio` | `float | None` | 否 | `None` | 过切拒收阈值（体积占比），留空使用默认 0.001。 | ≥ 0.0; ≤ 1.0 |
| `leftover_warn_ratio` | `float | None` | 否 | `None` | 残料告警阈值（体积占比），留空使用默认 0.01。 | ≥ 0.0; ≤ 1.0 |
| `leftover_reject_ratio` | `float | None` | 否 | `None` | 残料拒收阈值（体积占比），留空使用默认 0.05。 | ≥ 0.0; ≤ 1.0 |

### `FEMSolveRequest`

FEM 求解请求体（标准简支梁三点弯曲场景）。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `material` | `str` | 否 | `'steel45'` | 材料名称 | 最大长度: 64 |
| `elastic_modulus` | `float` | 否 | `210.0` | 弹性模量（GPa） | > 0; ≤ 1000 |
| `poisson_ratio` | `float` | 否 | `0.3` | 泊松比 | > 0; < 0.5 |
| `density` | `float` | 否 | `7850.0` | 密度（kg/m3） | > 0 |
| `yield_strength` | `float` | 否 | `355.0` | 屈服强度（MPa） | > 0; ≤ 100000 |
| `mesh_type` | `str` | 否 | `'tetrahedral'` | 网格类型 | 最大长度: 32 |
| `element_size` | `float` | 否 | `2.0` | 网格尺寸（mm） | > 0; ≤ 100 |
| `adaptive_refinement` | `bool` | 否 | `True` | 是否启用自适应细化 | - |
| `beam_length` | `float` | 否 | `100.0` | 试件长度（mm） | > 0; ≤ 10000 |
| `beam_width` | `float` | 否 | `20.0` | 试件宽度（mm） | > 0; ≤ 1000 |
| `beam_height` | `float` | 否 | `20.0` | 试件高度（mm） | > 0; ≤ 1000 |
| `load_force` | `float` | 否 | `5000.0` | 集中载荷（N） | > 0; ≤ 1000000000.0 |

### `SLDRequest`

稳定性叶图请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `machine_id` | `str` | 否 | `'vmc_850'` | 机床标识 | - |
| `tool_id` | `str` | 否 | `'endmill_d10'` | 刀具标识 | - |
| `speed_min` | `float` | 否 | `1000.0` | 起始转速 rpm | > 0 |
| `speed_max` | `float` | 否 | `10000.0` | 终止转速 rpm | > 0 |
| `num_points` | `int` | 否 | `100` | 每叶点数 | ≥ 20; ≤ 500 |
| `num_lobes` | `int` | 否 | `5` | 叶图数 | ≥ 1; ≤ 10 |
| `custom_modal` | `dict | None` | 否 | `None` | 自定义模态参数，覆盖机床默认值。字段：stiffness_z, damping_ratio, natural_freq, modal_mass | - |
| `actual_axial_depth` | `float | None` | 否 | `None` | 实际加工轴向切深 (mm)，用于精确计算不稳定转速区间 | > 0 |

### `ModalIdentificationRequest`

在线模态辨识请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `freqs` | `list[float]` | 是 | `-` | 频率序列 Hz | - |
| `re_frf` | `list[float]` | 是 | `-` | FRF 实部序列 mm/N | - |
| `im_frf` | `list[float]` | 是 | `-` | FRF 虚部序列 mm/N | - |
| `max_modes` | `int` | 否 | `3` | 最大辨识模态数 | ≥ 1; ≤ 8 |

### `PredictRequest`

单点稳定性预测请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `spindle_rpm` | `float` | 是 | `-` | 主轴转速 rpm | > 0 |
| `machine_id` | `str` | 否 | `'vmc_850'` |  | - |
| `tool_id` | `str` | 否 | `'endmill_d10'` |  | - |
| `axial_depth` | `float | None` | 否 | `None` | 实际轴向切深 mm，用于判定稳定性 | > 0 |

### `AdaptiveSolveSegmentRequest`

单段自适应求解请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `material` | `str` | 否 | `'45steel'` | 材料标识 | - |
| `cutter_diameter` | `float` | 否 | `10.0` | 刀具直径 mm | > 0 |
| `flute_count` | `int` | 否 | `4` | 刃数 | ≥ 1; ≤ 20 |
| `target_force_n` | `float` | 是 | `-` | 目标切削力 N | > 0 |
| `radial_depth_ae` | `float` | 否 | `5.0` | 径向切宽 mm | > 0 |
| `axial_depth_ap_init` | `float` | 否 | `5.0` | 初始轴向切深 mm（求解起点） | > 0 |
| `max_axial_depth` | `float` | 是 | `-` | 最大轴向切深 mm | > 0 |
| `min_axial_depth` | `float` | 是 | `-` | 最小轴向切深 mm | > 0 |
| `max_fz` | `float` | 是 | `-` | 最大每齿进给 mm/tooth | > 0 |
| `min_fz` | `float` | 是 | `-` | 最小每齿进给 mm/tooth | > 0 |
| `max_feed` | `float` | 是 | `-` | 机床最大进给 mm/min | > 0 |
| `min_feed` | `float` | 否 | `100.0` | 机床最小进给 mm/min | > 0 |
| `spindle_rpm` | `float` | 否 | `6000.0` | 主轴转速 rpm | > 0 |
| `stability_limit_ap` | `float | None` | 否 | `None` | 稳定性叶图极限切深 mm（可选约束） | > 0 |
| `kc1_1` | `float | None` | 否 | `None` | 比切削力 N/mm²（覆盖材料库） | > 0 |
| `mc` | `float | None` | 否 | `None` | 切削力指数（覆盖材料库） | > 0 |
| `safety_margin` | `float` | 否 | `0.85` | 安全裕度 (0,1] | > 0; ≤ 1.0 |
| `material_remainder_mm` | `float | None` | 否 | `None` | 该段剩余材料厚度 mm（可选约束） | > 0 |
| `force_override_n` | `float | None` | 否 | `None` | 该段目标力覆盖 N（可选） | > 0 |

### `KienzleComputeRequest`

Kienzle 正向切削力计算请求。

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `material` | `str` | 否 | `'45steel'` |  | - |
| `width` | `float` | 否 | `10.0` | 切削宽度 b mm | > 0 |
| `chip_thickness` | `float` | 否 | `0.1` | 未变形切屑厚度 h mm | > 0 |
| `kc1_1` | `float | None` | 否 | `None` |  | > 0 |
| `mc` | `float | None` | 否 | `None` |  | > 0 |

### `ProjectMetadataRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 否 | `'未命名工程'` | 工程名称 | 最大长度: 128 |
| `author` | `str` | 否 | `''` | 作者 | 最大长度: 64 |
| `description` | `str` | 否 | `''` | 工程描述 | 最大长度: 512 |

### `SaveRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `manifest` | `dict` | 是 | `-` | 完整的工程清单数据(project.json内容) | - |
| `project_id` | `str` | 否 | `''` | 工程ID（保存已有工程时使用） | - |
| `output_name` | `str` | 否 | `''` | 输出文件名（另存为时使用） | - |

### `OpenRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `file_path` | `str` | 否 | `''` | 要打开的 .ljm 文件路径 | - |
| `upload_data` | `str | None` | 否 | `None` | Base64编码的.ljm文件数据 | - |

### `ResourceUploadMeta`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `resource_type` | `str` | 否 | `'model'` | 资源类型 | 正则: `^(drawing|model|toolpath|simulation|postprocessor|extension)$` |
| `metadata` | `dict[str, Any]` | 是 | `-` | 额外元数据 | - |

### `StepImportResponse`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `file_name` | `str` | 否 | `''` |  | - |
| `file_size` | `int` | 否 | `0` |  | - |
| `parse_time_ms` | `float` | 否 | `0.0` |  | - |
| `conversion_time_ms` | `float` | 否 | `0.0` |  | - |
| `model_info` | `dict` | 是 | `-` |  | - |
| `entities` | `list[dict]` | 是 | `-` |  | - |
| `is_assembly` | `bool` | 否 | `False` |  | - |
| `stl_files` | `list[dict]` | 是 | `-` |  | - |
| `brep_files` | `list[dict]` | 是 | `-` |  | - |
| `status` | `dict` | 是 | `-` |  | - |
| `warnings` | `list[str]` | 是 | `-` |  | - |
| `cached` | `bool` | 否 | `False` |  | - |
| `import_id` | `str` | 否 | `''` |  | - |
| `format` | `str` | 否 | `''` |  | - |

### `ConditionItem`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `parameter` | `str` | 是 | `-` |  | - |
| `operator` | `str` | 是 | `-` |  | - |
| `value` | `str` | 是 | `-` |  | - |
| `unit` | `str | None` | 否 | `None` |  | - |

### `ResultItem`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `parameter` | `str` | 是 | `-` |  | - |
| `operator` | `str` | 是 | `-` |  | - |
| `value` | `str` | 是 | `-` |  | - |
| `unit` | `str | None` | 否 | `None` |  | - |

### `RuleCreateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `description` | `str` | 否 | `''` |  | - |
| `group_id` | `int | None` | 否 | `None` |  | - |
| `conditions` | `list[ConditionItem]` | 是 | `-` |  | - |
| `logic_operator` | `str` | 否 | `'AND'` |  | - |
| `result` | `ResultItem` | 是 | `-` |  | - |
| `status` | `str` | 否 | `'active'` |  | - |
| `priority` | `int` | 否 | `0` |  | - |

### `RuleUpdateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str | None` | 否 | `None` |  | - |
| `description` | `str | None` | 否 | `None` |  | - |
| `group_id` | `int | None` | 否 | `None` |  | - |
| `conditions` | `list[ConditionItem] | None` | 否 | `None` |  | - |
| `logic_operator` | `str | None` | 否 | `None` |  | - |
| `result` | `ResultItem | None` | 否 | `None` |  | - |
| `status` | `str | None` | 否 | `None` |  | - |
| `priority` | `int | None` | 否 | `None` |  | - |

### `GroupCreateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str` | 是 | `-` |  | - |
| `description` | `str` | 否 | `''` |  | - |

### `GroupUpdateRequest`

| 字段名 | 类型 | 必填 | 默认值 | 说明 | 约束 |
|--------|------|------|--------|------|------|
| `name` | `str | None` | 否 | `None` |  | - |
| `description` | `str | None` | 否 | `None` |  | - |


---

*本文档由 API 文档自动生成系统生成，如有疑问请联系开发团队。*
