# 更新日志（Changelog）

本文件记录灵境制造的版本变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 修复（Fixed）

**CI / 版本管理**

- 单元测试 job 的 pytest 命令中，两行注释写在续行符之后、`-m "not skip_ci"` 之前，
  bash 将注释拼入命令并提前终止，`-m` 沦为独立命令（exit 127）：skip_ci 过滤从未生效，
  且该步骤即便测试全绿也必然失败。注释移出命令，并补 `--timeout=300`（与 full suite 一致）。
- 本地环境守卫 `tests/unit/test_environment_check.py`（断言 Python 3.14、desktop_runtime 存在）
  标注 `skip_ci`，不再在 Linux CI 上必挂；硬编码的 `<user>` 占位路径改为 `sys.executable`。
- 全部工作流统一 Node 22（ci/pr/post-merge/dependency-scan 此前为已 EOL 的 Node 18，
  与 release/desktop-build 的 22 不一致）；pnpm 由根 `package.json` 的 `packageManager`
  字段钉死为 9.15.9，工作流不再浮动指定 `version: 9`；补 `engines.node >= 22`。
- 发版流水线新增 tag 与 `VERSION` 文件一致性校验（tag 推送路径），杜绝"tag v2.9.0 打出
  2.8.0 产物"；发布说明改为从本文件提取对应版本小节，不再用 `git log` 覆盖 CHANGELOG.md。
- dependency-scan 对齐 Python 3.12；pnpm audit 步骤名与其非阻断行为一致，stderr 不再混入 JSON 产物。

**后端稳定性**

- **NL2CAD 沙箱迁移到可强杀子进程**（修复 Linux 挂死）：退化几何（如
  `box(0,0,0)`）在 cadquery-ocp Linux 轮子上于 OCCT 原生层挂死，原「子线程 +
  异步异常」超时无法打断原生调用，泄漏线程令同进程后续 CadQuery 调用一并卡死
  （Docker 生产镜像为 Linux，LLM 产出退化几何时线上请求同样会挂）。现父进程把
  脚本派发到一次性子进程回环执行，沙箱约束（AST 审计/受限内置/反射过滤/RLIMIT）
  逐字保留，超时由 `subprocess.run` 强杀兜底；`test_zero_dim_then_valid` 解除
  `skip_ci` 回归 CI，新增 `tests/unit/test_cadquery_sandbox.py` 覆盖审计前置/
  异常回传/反射逃逸拦截/强杀路径。

**后端 / 工程化**

- `app/rules/api.py` 在补齐缺失的请求模型后从未重新纳入 mypy 检查：修复
  `_validate_rule_data` 签名（创建路径传请求模型、更新路径传 DB 模型，改用
  Protocol 结构化约束避免联合类型迭代退化）与 `list_groups` 可空主键守卫，
  3 处 mypy 错误清零；同步撤销 `mypy.ini` 中已失去前提的
  `exclude = app/rules/api\.py`。
- 仓库治理：`output/` 中 4 个个人求职/自荐材料移出 git 追踪（磁盘文件保留，
  借助既有 `output/` 忽略规则不再进入公开仓快照）；清理根目录 5 个
  `.tmp_*.log` 一次性日志。

### 变更（Changed）

**文档与代码现状同步**

- `AGENTS.md` / `PROJECT_OVERVIEW.md` / `README.md`：移除对已删除 `shared/`
  目录的引用；后处理器数量统一为「9 种内置 + YAML 方言包」（原「11 种」为
  过时口径）；巨型组件/巨型文件状态更新（V2.8.0 已拆完前端巨型组件与 4/5
  后端巨型文件，剩余 `agent/orchestrator.py`）。

## [2.8.0] - 2026-09-05

本版本为质量专项版本：以稳定性修复、重复代码收敛、仓库结构与内容治理为主线，
不含新功能，对外接口（REST API 契约、前端交互）保持兼容。

### 修复（Fixed）

**后端稳定性**

- **全局异常中间件**（`app/core/middleware.py`）：
  - 500 响应中的路径脱敏条件失效（本机用户目录原样返回客户端），统一复用 `LogSanitizer.sanitize_paths`；
  - 环境判定改为服务端 `ENVIRONMENT` 环境变量，不再信任客户端可伪造的 `x-environment` 请求头；
  - 修复 `exc_detail=` 非法日志关键字导致 AppException 分支触发即 TypeError 的问题。
- **MTConnect 采集**（`app/integrations/mtconnect/adapter.py`）：TDengine 持久化失败时采样数据不再静默丢弃，
  失败批次回灌缓冲区重试（有界 `max_buffer`，溢出计入 `dropped_samples`）。
- **成本预算 API**（`app/api/v1/cost_budget.py`）：20 个端点由 `async def` 改为普通 `def`，
  同步 SQLite 查询不再阻塞整个事件循环（含全部 WebSocket）。
- **实时监控 WS**（`app/api/v1/monitor_ws.py`）：Agent 掉线降级演示数据时在 payload 中标记 `source: "demo"`；
  断连后周期性重探并自动恢复真实数据源；补捕 XML 解析异常（原先会杀死整个连接）。
- **LNN 预测器加载**（`app/services/explainability/_predictor_loader.py`）：补全模型加载路径——
  原实现缓存未命中时写入并返回 `None`，加载从未真正生效。
- **llama.cpp Provider URL 拼接**：修复 base_url 已含 `/v1` 时产生 `/v1/v1/` 双前缀的问题。
- 其他：SQLite 方言探测失败留痕（`state/manager.py`）、数据集探测异常留痕（`training/contract_adapter.py`）、
  matplotlib figure 异常路径泄漏（`ai/lnn/visualization.py`）、技能监视线程窄捕致死（`plugins/skill_loader/lifecycle.py`）、
  熔断器上下文死代码清理、工作台模型列表端点 404 修复（`WorkspaceModelsTab.vue`）。

### 变更（Changed）

**后端重复代码收敛（复用率提升）**

- 新增 `app/utils/task_store.py`：任务存储双基类（`PerTaskJsonStore` / `InMemoryTaskStore`），
  chatter / cutting_parameters / gcode_generation / cam_validation 四份同构实现收敛为约 20 行的差异声明。
- 新增 `app/ai/llm/providers/openai_compat_base.py`：本地 OpenAI 兼容 Provider 预设基类，
  lmstudio / llamacpp / vllm / tgi / koboldcpp 五份逐行拷贝（约 630 行）收敛为 preset 声明。
- 新增 `app/utils/dict_utils.py`（`deep_merge` 三合一）、`app/utils/id.py`（实体 ID 生成）、
  `app/utils/time.py:utcnow_seconds_iso_z`（秒级时间戳，CAM 校验与规则库共用）；
  知识图谱抽取器的 JSON 提取委托 `app/utils/utils.extract_json_text`；kg 本地 LLM 配置改读 `config.ai.AIConfig`。
- `app/benchmarks/performance/_bootstrap.py`：10 个基准脚本的 `sys.path` 引导收敛为共享模块。

**前端重构**

- 新增 `composables/sseConnection.ts`：SSE 连接通用核心（epoch 竞态防护 + 指数退避重连），
  `useEventSource` 与 `useWorkflowStream` 约 180 行的双份实现收敛为单一实现。
- 新增 `composables/useEChart.ts`：ECharts 生命周期样板统一，三个成本图表组件接入。
- `utils/formatters.ts` 新增 `formatDateTimeSafe`，收敛 stores/快照面板各自维护的 `formatTime` 实现。

### 移除（Removed）

- **僵尸目录/文件**（验证零引用后删除）：根 `src-tauri/`（缺 `tauri.conf.json` 的陈旧副本）、
  `skills/`（147 文件第三方拷贝）、根 `vite.config.ts` / `vitest.config.ts` / `tsconfig.json` / `tsconfig.node.json`、
  `version-sync.cjs`（指向不存在文件，运行必失败）、`inject-token.js`（含过期令牌的调试残留）、
  `test-results/`、孤儿基准 `renderFPS.bench.ts`、4 个硬编码绝对路径的一次性补丁脚本。
- **前端死代码**：`features/` API 层（16 模块零消费）、未挂载组件 `MachineMonitor` / `ExperienceCapture` /
  `ParameterRecommendPanel` 及其测试、零消费者的 `experienceStore` 与 `defineCrudStore` 工厂、
  过期组件拷贝 `task_board/TaskCard.vue`、`base/DXFImportDialog.vue` + `BaseImportDialog.vue`。
- **AI 痕迹注释**：清除约 22 处日期戳/任务编号式 changelog 注释与清理过程叙事，保留技术警示。

### 安全（Security）

- 错误响应路径脱敏修复（详见修复一节）；删除含过期 admin 令牌的 `inject-token.js`。

### CI/工程化（Changed）

- 修复 `pr.yml` / `ci.yml` 前端 lint 与 type-check 步骤缺失 `working-directory: engineering` 导致的失效。
- 移除两处永久空转的 "3D rendering FPS benchmark" 步骤。
- `scripts/generate_icon.py` 图标输出目标修正为 `engineering/src-tauri/icons`。
- 根 `package.json` 新增 `test` / `lint` / `type-check` 路由脚本（转发 engineering 工具链）。
- `PROJECT_OVERVIEW.md` 仓库结构描述与实际布局同步。

### 兼容性说明

- `GET /api/v1/cost-budget/*` 响应结构与状态码不变；端点并发模型变化对客户端透明。
- 监控 WebSocket 新增 `source` 字段（`demo`/`agent`），前端可选用；未读取该字段的客户端行为不变。
- 生产环境（`ENVIRONMENT=production`）下 500 响应不再返回详细 traceback（开发环境不变且已脱敏）。

## [2.7.0] - 2026-08-19

分支收敛版本：refactor 分支并入 main（旧 main 存档于 tag `backup/main-2026-08-03`），
工程侧与科研侧物理解耦（engineering / research / shared 三层）。
