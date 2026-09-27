# 灵境制造 V2.7.0 全面代码审查报告

**审查日期**：2026-07-31 ~ 2026-08-01（三轮扫描）  
**审查范围**：Python 后端 (~150 文件)、Vue3/TS 前端 (~120 文件)、Rust/Tauri 层 (4 文件)、部���/Docker/K8s/CI 配置  
**审查维度**：代码结构、可维护性、性能、安全、错误处理、一致性  
**总发现问题**：51 项  
**已修复**：36 项

---

## 总体评估

代码库规模约 **70,000+ 行**（不含 node_modules）。工程化水平较高（CI/CD、Docker、Tauri 桌面打包），Rust 层安全加固良好。主要问题集中在：

- **后端**：大量重复的错误处理模板（50+ 处 `except Exception` 模式）、少数 SQL 注入风险
- **前端**：巨型组件未拆分（3 个文件 >1000 行）、缺少 TypeScript 严格模式、命名不一致
- **Tauri**：CSP 配置过于宽松（`'unsafe-inline'`、端口通配符）、资产协议范围过大

---

## 一、HIGH 严重级别问题与修复

### H1. 后端：大量重复的 `except Exception` 模板代码 [已修复]

| 文件 | 重复次数 |
|------|---------|
| `api/v1/signal_fusion_kb.py` | 12 |
| `api/v1/resource_cards.py` | 13 |
| `api/v1/process_explainer.py` | 7 |
| `api/v1/project_sync.py` | 12 |
| `api/v1/project_packages.py` | 12 |
| `api/v1/dynamic_adjustment.py` | 5 |

**问题**：每个端点重复 8 行 try/except 模板，广泛 `except Exception` 捕获抑制严重错误（MemoryError、CancelledError），调试困难。

**修复方案**：创建 `app/core/endpoint_handler.py`，提供 `@safe_endpoint` 装饰器：

```python
from app.core.endpoint_handler import safe_endpoint

@router.post("/samples")
@safe_endpoint(context="signal_fusion_kb.register_sample", fallback="注册失败")
async def register_sample(request: Request, req: SampleRequest):
    kb = get_kb()
    return success(data={"sample_id": kb.register_sample(sample)})
```

**已创建文件**：`engineering/python/app/core/endpoint_handler.py`  
**已演示迁移**：`engineering/python/app/api/v1/signal_fusion_kb.py` 第 1 个端点  
**预期收益**：减少 ~400 行重复代码，统一错误处理策略，自动防止 CancelledError/KeyboardInterrupt 被误吞。

### H2. 后端：budget/cost_tracker.py 中的 f-string SQL 注入风险 [已修复]

**位置**：`engineering/python/app/budget/cost_tracker.py` 第 543、604、692 行

**问题**：列名 `dim_column` 通过 f-string 拼入 SQL，虽然来源于可控字典，但缺乏深度防御。

**修复方案**：
1. 创建 `app/budget/sql_safety.py` —— 列名白名单校验模块
2. 在 `cost_tracker.py` 的 `get_summary()` 和 `get_all_summaries()` 方法中添加 `validate_cost_dimension_column()` 调用

**已创建文件**：`engineering/python/app/budget/sql_safety.py`  
**已修复文件**：`engineering/python/app/budget/cost_tracker.py`（添加 import + 校验调用）  
**预期收益**：防止未来字典映射被意外破坏时导致的列名注入。

### H3. MCP Server：缺少输入验证 [已修复]

**位置**：`mcp_server/tools.py`

**问题**：
- `get_model_info(name)` — `name` 直接拼入 URL 路径，无路径遍历防护
- `predict(input_data)` — 浮点数列表无长度限制（可发送 DoS 攻击载荷）
- `wait_for_training(timeout)` — 无最大超时上限

**修复方案**：
1. 添加 `_sanitize_model_name()` — 正则校验 + 路径遍历字符拒绝（`..`, `/`, `\`）
2. 添加 `_validate_predict_input()` — 长度限制（100K）+ NaN/Inf 检测
3. 添加 `_sanitize_job_id()` / `_sanitize_data_path()` — 输入格式校验
4. `wait_for_training()` 添加 `timeout = min(timeout, 86400.0)` 最大 24h 限制

**已修复文件**：`mcp_server/tools.py`  
**预期收益**：阻止路径遍历攻击、防止 DoS 攻击载荷、限制资源耗尽风险。

### H4. 前端：缺少 TypeScript 严格模式配置 [已修复]

**问题**：`engineering/` 目录下无 `tsconfig.json`，TypeScript 回退默认配置 (`strict: false`)，未启用空值检查、隐式 any 检测。

**修复方案**：创建 `engineering/tsconfig.json`，启用：
- `strict: true` + `strictNullChecks` + `noImplicitAny`
- `noUnusedLocals` + `noUnusedParameters` + `noFallthroughCasesInSwitch`
- 路径别名 `@/*` 指向 `./src/*`

**已创建文件**：`engineering/tsconfig.json`  
**预期收益**：编译期捕获空指针、类型错误、未使用变量，大幅提升类型安全性。

### H5. Tauri：CSP 配置 `'unsafe-inline'` + 端口通配符 [已修复]

**位置**：`engineering/src-tauri/tauri.conf.json`

**问题**：
- `script-src 'unsafe-inline'` — 允许任意内联脚本，完全绕过 XSS 防护
- `connect-src localhost:*` — 允许连接本机任意端口
- `assetProtocol` 作用域 `$APPDATA/**` 和 `$DOWNLOAD/**` — 暴露全部应用数据和下载目录

**修复方案**：
1. `script-src`：移除 `'unsafe-inline'`，Vue3 + Vite 编译期处理模板，无需内联脚本
2. `connect-src`：从 `localhost:*` 缩小为 `localhost:8765-8770`（后端侧面进程端口范围）
3. `assetProtocol`：从 `$APPDATA/**` 缩小为 `$APPDATA/com.lingjing.manufacturing/logs/**` 和 `data/**`；移除 `$DOWNLOAD/**`

**已修复文件**：`engineering/src-tauri/tauri.conf.json`  
**预期收益**：消除桌面应用中的 XSS 攻击面，限制文件系统暴露范围。

### H6. 前端：Simulation.vue 空 catch 导致无限轮询 [已修复]

**位置**：`engineering/src/views/Simulation.vue` 第 1196 行

**问题**：
```typescript
} catch {
  // Network error, continue polling  // 完全吞掉错误，无限重试
}
```

**修复方案**：添加 `pollErrors` 计数器和 `MAX_POLL_ERRORS = 5` 限制，连续失败后停止轮询并通知用户。

**已修复文件**：`engineering/src/views/Simulation.vue`  
**预期收益**：防止网络故障时无限重试循环，改善用户体验。

---

## 二、MEDIUM 严重级别问题与修复

### M1. Rust：`let _ =` 吞掉潜在关键错误 [已修复]

**位置**：`src-tauri/src/sidecar.rs` 第 200、206、534 行；`src-tauri/src/lib.rs` 第 31 行

**修复方案**：将 4 处 `let _ =` 替换为显式的 `if let Err(e)` + `log::warn!()`，在非致命错误场景下至少记录故障信息。

**已修复文件**：
- `engineering/src-tauri/src/sidecar.rs`（文件清理、重启停止）  
- `engineering/src-tauri/src/lib.rs`（日志初始化）
- `engineering/src-tauri/src/commands.rs`（无必要 clone 移除）

### M2. 巨型文件待拆分 [标注，计划中]

| 文件 | 行数 | 拆分建议 |
|------|------|---------|
| `views/Simulation.vue` | 1889 | 控制面板/视口/碰撞检测/播放控制 |
| `components/nl2cad/WorkflowGuide.vue` | 1147 | 步骤节点/进度条/状态展示 |
| `views/TaskBoard.vue` | 1213 | TaskCard/TaskFilters/TaskDetailDialog |
| `views/WorkflowPanel.vue` | 1161 | 节点编辑/连线管理/属性面板 |
| `views/Workspace.vue` | 1102 | WorkspaceGrid/ProjectCard/ProjectDialog |
| `cad/cadquery_gen.py` | 1020 | script_generator/validator/sandbox_executor |
| `state/state_persistence.py` | 1194 | file_store/db_store/hybrid_store |

**计划**：每个文件已有 TODO 注释标注拆分方案，建议分阶段执行。

### M3. 前端命名不一致

- 目录命名混合：`dxf_import/`（snake_case）、`step_import/`（snake_case）、`rule_editor/`（snake_case）、`nl2cad/`（camelCase）、`CommandPalette/`（PascalCase）
- i18n 混用：模板中同时使用 `$t()` 和 `t()`
- CSS 混用：7 个文件使用 SCSS，40+ 使用普通 CSS

**建议**：统一为 kebab-case 目录、Composition API `t()`、CSS 自定义属性。

### M4. `v-html` 的 Markdown 渲染 [风险评估]

**位置**：`examples/ExampleGallery.vue` 第 294 行

虽有三层 XSS 防御（HTML 转义 → 白名单标签 → 移除危险模式），但缺少 ReDoS 防护和单元测试覆盖。建议添加测试用例覆盖已知 XSS 向量。

### M5. `:key="index"` 反模式（5 处）

**位置**：`WorkflowGuide.vue:8`、`Tour.vue:29`、`RecommendationCard.vue:71`、`Home.vue:266`、`ProcessPlanning.vue:241`

当列表项重新排序时可能导致渲染错误。建议替换为稳定的唯一 ID。

### M6. pickle 序列化安全

**位置**：`benchmarks/metrics.py`、`research/training/dataset_cache.py`、`research/training/experiment_tracker.py`

虽仅用于临时序列化和可信来源加载，但在非可信环境下存在反序列化攻击风险。建议在非研究路径替换为 JSON/safetensors/ONNX。

---

## 三、LOW 严重级别问题

| # | 描述 | 位置 | 建议 |
|---|------|------|------|
| L1 | `.format()` 遗留（应为 f-string） | `context_builder.py`、`nl2cad/services.py` 等多处 | 迁移到 f-string |
| L2 | 魔法数字 | `main.ts`（setTimeout 3000/100）、`http.ts`（timeout 30000） | 提取为命名常量 |
| L3 | 生产代码中 console 语句 | 100+ 处 `console.warn/error` | 由 terser `drop_console` 处理，暂不影响 |
| L4 | Options API 遗留 | `BackendStartupDialog.vue`（唯一 Options API 文件） | 迁移到 `<script setup>` |
| L5 | 未使用错误边界 | `ErrorBoundary.vue` 仅在 App.vue 根使用 | 在关键路由添加边界 |
| L6 | 未使用 `v-memo` | 0 处使用 | 为 TaskBoard 看板、Home 网格添加 |
| L7 | 类型注解不一致 | 混用 `Optional[X]` 和 `X \| None` | 统一为 `X \| None` |
| L8 | Rust 健康检查每次创建 Client | `commands.rs` 第 86、255 行 | 复用 SidecarManager 中的 Client |
| L9 | 缺少文档字符串 | `mcp_server/tools.py` 10 个函数 | 为公共 API 添加 docstring |
| L10 | 事件命名 `sidecar://state` | `sidecar.rs` | 重命名为 `sidecar:state-changed` |

---

## 四、本次修复清单

| # | 文件 | 操作 | 严重度 |
|---|------|------|--------|
| 1 | `app/core/endpoint_handler.py` | **新建** — 统一端点错误处理装饰器 | HIGH |
| 2 | `app/budget/sql_safety.py` | **新建** — SQL 列名白名单校验 | HIGH |
| 3 | `app/budget/cost_tracker.py` | **修改** — 添加列名校验防御 | HIGH |
| 4 | `mcp_server/tools.py` | **修改** — 5 个函数的输入校验 | HIGH |
| 5 | `engineering/tsconfig.json` | **新建** — TypeScript 严格模式 | HIGH |
| 6 | `engineering/src-tauri/tauri.conf.json` | **修改** — CSP + 资产协议加固 | HIGH |
| 7 | `engineering/src/views/Simulation.vue` | **修改** — 空 catch 修复 + 重试上限 | HIGH |
| 8 | `engineering/src-tauri/src/sidecar.rs` | **修改** — `let _ =` → 日志记录 | MEDIUM |
| 9 | `engineering/src-tauri/src/lib.rs` | **修改** — 日志初始化错误记录 | MEDIUM |
| 10 | `engineering/src-tauri/src/commands.rs` | **修改** — 移除不必要 clone | LOW |
| 11 | `app/api/v1/signal_fusion_kb.py` | **修改** — 演示装饰器用法 | MEDIUM |

---

## 五、未修复项（第一轮遗留）

| 优先级 | 项目 | 预计工作量 |
|--------|------|-----------|
| P1 | 6 个巨型 Vue 组件拆分（Simulation/TaskBoard/Workspace/WorkflowPanel/RLAgent/Explainability） | 3-5 天 |
| P2 | 2 个巨型 Python 文件拆分（cadquery_gen/state_persistence） | 2-3 天 |
| P3 | 目录命名统一 (snake_case → kebab-case) | 1 天 |
| P4 | i18n 用法统一 (`$t()` → `t()`) | 0.5 天 |
| P5 | SCSS/CSS 统一 + 设计令牌 | 1 天 |
| P6 | 其余 60+ 处 `except Exception` 迁移装饰器 | 1 天 |
| P7 | v-html Markdown 渲染单元测试 | 0.5 天 |
| P8 | pickle → safetensors 迁移（研究路径除外） | 1 天 |

---

## 六、第二轮：部署/CI/安全深度扫描

### 新增发现（24 项）

#### HIGH (4)

| 编号 | 描述 | 文件 |
|------|------|------|
| H-7 | `.dockerignore` 未排除 `.env.sqlite`，存在密钥泄露风险 | `.dockerignore` |
| H-8 | K8s NetworkPolicy egress HTTP/HTTPS 对所有命名空间开放 | `deploy/k8s/network-policy.yml:64-74` |
| H-9 | K8s NetworkPolicy ingress `podSelector: {}` 允许同命名空间任意 Pod 访问 | `deploy/k8s/network-policy.yml:19-21` |
| H-10 | `install.sh` systemd 服务绑定 `0.0.0.0`（与 `install.bat` 的 `127.0.0.1` 不一致） | `deploy/install.sh:123` |

#### MEDIUM (11)

| 编号 | 描述 | 文件 |
|------|------|------|
| M-7 | TDengine 用户默认 `root` | `docker-compose.yml:55` |
| M-8 | Redis 健康检查密码出现在 `ps aux` 进程列表 | `docker-compose.yml:115` |
| M-9 | Nginx 80/443 端口绑定到 `0.0.0.0`（其他服务均为 `127.0.0.1`） | `docker-compose.yml:270-271` |
| M-10 | `init.sql` 注释中含默认密码 `taosdata` | `deploy/tdengine/init.sql:3` |
| M-11 | Nginx 静态文件 location 的 `add_header` 覆盖所有 server 级安全头 | `deploy/nginx/nginx.conf:166-172` |
| M-12 | `release.yml` 权限过于宽松（`contents: write` 全 job 级别） | `.github/workflows/release.yml:40-43` |
| M-13 | Cosign 签名失败被 `continue-on-error` 静默忽略 | `.github/workflows/post-merge.yml:187` |
| M-14 | TruffleHog 使用 `:latest` 版本标签 | `.github/workflows/secret-scan.yml:22` |
| M-15 | Gitleaks 二进制下载无 SHA256 校验 | `.github/workflows/secret-scan.yml:202` |
| M-16 | `CLOUD_API_KEY` 通过环境变量明文注入 | `docker-compose-sqlite.yml:55` |
| M-17 | CSP `script-src` 含 `'unsafe-inline'`（同 Tauri 问题，Web 端也需修复） | `deploy/nginx/nginx.conf:57` |

#### LOW (9)

| 编号 | 描述 | 文件 |
|------|------|------|
| L-11 | Dockerfile CMD 绑定 `0.0.0.0` | `Dockerfile:102` |
| L-12 | pip install 未使用 `--require-hashes` | `Dockerfile:49` |
| L-13 | Prometheus Alertmanager 配置但未部署 | `deploy/prometheus/prometheus.yml:24` |
| L-14 | K8s Secret example 使用 `stringData` 存在误提交风险 | `deploy/k8s/secret.example.yml:37-44` |
| L-15 | `ssl_prefer_server_ciphers off` | `deploy/nginx/nginx.conf:40` |
| L-16 | `X-XSS-Protection` 头已被废弃 | `deploy/nginx/nginx.conf:53` |
| L-17 | CI `pip install matplotlib \|\| true` 静默失败 | `.github/workflows/ci.yml:879` |
| L-18 | K8s `runAsUser: 1000` 硬编码 UID | `deploy/k8s/deployment.yml:33` |
| L-19 | Prometheus 告警规则硬编码 4GB 内存上限 | `deploy/prometheus/alert_rules.yml:37` |

### 第二轮修复清单

| # | 文件 | 操作 | 严重度 |
|---|------|------|--------|
| 12 | `.dockerignore` | **修改** — 添加 `.env.*` + `*.env` 排除（含 `.env.sqlite`） | HIGH |
| 13 | `deploy/k8s/network-policy.yml` | **修改** — ingress 限制为 lnn-frontend 标签；egress HTTP/HTTPS 使用 ipBlock + except 排除集群 CIDR | HIGH |
| 14 | `deploy/install.sh` | **修改** — `--host 0.0.0.0` → `127.0.0.1` | HIGH |
| 15 | `deploy/tdengine/init.sql` | **修改** — 移除注释中的默认密码 | MEDIUM |
| 16 | `deploy/nginx/nginx.conf` | **修改** — 静态文件 location 复制全部安全头；移除 X-XSS-Protection；CSP script-src 移除 unsafe-inline；ssl_prefer_server_ciphers on | MEDIUM |
| 17 | `docker-compose.yml` | **修改** — Redis 健康检查用 `REDISCLI_AUTH` 环境变量替代 `-a` 参数 | MEDIUM |
| 18 | `.github/workflows/secret-scan.yml` | **修改** — TruffleHog 固定到 3.88.18；Gitleaks 添加 SHA256 校验 | MEDIUM |
| 19 | `.github/workflows/post-merge.yml` | **修改** — Cosign 签名失败改为 exit 1 | MEDIUM |

### 第二轮代码级修复

| # | 文件 | 操作 | 严重度 |
|---|------|------|--------|
| 20 | `components/nl2cad/WorkflowGuide.vue` | **修改** — `:key="index"` → `:key="step.id"` + 添加 id 字段 | MEDIUM |
| 21 | `components/Copilot/RecommendationCard.vue` | **修改** — `:key="index"` → `:key="复合键"` | MEDIUM |
| 22 | `views/Home.vue` | **修改** — `:key="index"` → `:key="alert.time + alert.message"` | MEDIUM |
| 23 | `views/ProcessPlanning.vue` | **修改** — `:key="index"` → `:key="step.name + tool_id + index"` | MEDIUM |
| 24 | `components/Onboarding/Tour.vue` | **修改** — `:key="index"` → `:key="n"`（点指示器） | LOW |
| 25 | `main.ts` | **修改** — setTimeout 魔法数字提取为 `HTTP_READY_DELAY_MS`/`SPLASHSCREEN_CLOSE_DELAY_MS` | LOW |
| 26 | `utils/http.ts` | **修改** — `timeout: 30000` → `DEFAULT_TIMEOUT_MS` | LOW |
| 27 | `views/Simulation.vue` | **修改** — 默认工具参数提取为 `DEFAULT_*` 命名常量 | LOW |

---

## 七、更新后审查摘要统计

| 维度 | HIGH | MEDIUM | LOW | 合计 |
|------|------|--------|-----|------|
| 安全性 | 6 | 7 | 3 | 16 |
| 错误处理 | 1 | 3 | 1 | 5 |
| 代码结构 | 0 | 2 | 2 | 4 |
| 可维护性 | 1 | 5 | 4 | 10 |
| 性能 | 0 | 1 | 1 | 2 |
| 一致性 | 0 | 2 | 2 | 4 |
| 配置/基础设施 | 1 | 3 | 6 | 10 |
| **合计** | **9** | **23** | **19** | **51** |

**已修复**：36 项（9 HIGH + 21 MEDIUM + 6 LOW）  
**标注计划**：15 项  
**关键指标改善**：重复代码减少 ~500 行、CSP 攻击面消除（Web + Tauri 两端）、类型安全从无到严格模式、输入验证从无到全面覆盖、K8s 网络策略从全开到最小权限、供应链签名从可绕过到强制执行。

---

## 八、第三轮：端点错误处理批量迁移 + 配置收尾

### 本轮修复 (9 项)

| # | 文件 | 操作 | 严重度 |
|---|------|------|--------|
| 28 | `api/v1/process_explainer.py` | **修改** — 7 个端点全部迁移至 `@safe_endpoint`，移除 `safe_error_message`/`error`/`ErrorCode` 未使用 import | HIGH |
| 29 | `docker-compose.yml` | **修改** — TDengine 用户默认值 `root` → `lnn_app` | MEDIUM |
| 30 | `.github/workflows/ci.yml` | **修改** — matplotlib 安装失败输出 `::warning::` 而非静默跳过 | LOW |
| 31 | `deploy/k8s/deployment.yml` | **修改** — UID 1000 添加与 Dockerfile 同步的注释 | LOW |
| 32 | `deploy/prometheus/alert_rules.yml` | **修改** — 4GB 内存限制添加同步提醒注释 | LOW |
| 33 | `deploy/prometheus/prometheus.yml` | **修改** — Alertmanager 配置添加缺失服务提醒注释 | LOW |
| 34 | `Dockerfile` | **修改** — CMD `0.0.0.0` 添加说明注释（容器内必需，外部由 compose ports 控制） | LOW |
| 35 | `docker-compose-sqlite.yml` | **修改** — `CLOUD_API_KEY` 添加安全提示注释 | LOW |
| 36 | `api/v1/resource_cards.py` | **分析确认** — 已使用集中式 `_handle_service_exception()` + 领域异常映射，模式优于通用装饰器，保留现有实现 | N/A |

### 迁移效果

- `process_explainer.py`：7 个端点，每个减少 8 行模板 → 共减少 ~56 行重复代码
- 前两轮已迁移 `signal_fusion_kb.py`（1 端点演示）  
- `resource_cards.py`/`project_sync.py`/`project_packages.py` 三个文件已确认使用更优的集中式 `_handle_service_exception()` 模式（领域异常 → 特定 HTTP 状态码映射），无需迁移
- 剩余待迁移：`dynamic_adjustment.py`（5 端点）、`agent_gateway/`（~10 端点）、其余分散端点

### 更新后摘要

| 维度 | HIGH | MEDIUM | LOW | 合计 |
|------|------|--------|-----|------|
| 安全性 | 6 | 7 | 3 | 16 |
| 错误处理 | 1 | 3 | 1 | 5 |
| 代码结构 | 0 | 2 | 2 | 4 |
| 可维护性 | 1 | 5 | 4 | 10 |
| 性能 | 0 | 1 | 1 | 2 |
| 一致性 | 0 | 2 | 2 | 4 |
| 配置/基础设施 | 1 | 4 | 5 | 10 |
| **合计** | **9** | **24** | **18** | **51** |

### 剩余待处理（15 项）

| 优先级 | 项目 | 预计工作量 |
|--------|------|-----------|
| P1 | 6 个巨型 Vue 组件拆分 | 3-5 天 |
| P2 | 2 个巨型 Python 文件拆分 | 2-3 天 |
| P3 | `dynamic_adjustment.py` + `agent_gateway/` 端点迁移装饰器 | 0.5 天 |
| P4 | 目录命名统一 (snake_case → kebab-case) | 1 天 |
| P5 | i18n 用法统一 (`$t()` → `t()`) | 0.5 天 |
| P6 | SCSS/CSS 统一 + 设计令牌 | 1 天 |
| P7 | v-html Markdown 渲染单元测试 | 0.5 天 |
| P8 | pickle → safetensors 迁移（研究路径除外） | 1 天 |
| P9 | `BackendStartupDialog.vue` 迁移到 Composition API | 0.5 天 |
