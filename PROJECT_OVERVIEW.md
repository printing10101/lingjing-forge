# 灵境制造（Virtual Realm Manufacturing）· 项目概览

> 探查时间：2026-07-29　|　更新时间：2026-09-17　|　当前版本：V2.8.0　|　当前分支：`main`
> 仓库根目录：`D:\灵境制造`

---

## 1. 项目用途（What）

灵境制造是**装在工厂自己电脑里的制造物理 AI**（Desktop Physical AI for Machining），面向中小型制造企业的 CAM 工艺编程痛点：

- **低门槛**：把 DXF/STEP 工程图自动解析、3D 重建、工艺规划、刀具路径仿真、NC 后处理全流程搬到本地桌面；
- **高效率**：以 LNN（液体神经网络）+ 本地 LLM（Ollama 等）+ RAG 工艺知识库驱动，减少反复试切；
- **数据主权**：LLM 推理、工艺库、LNN 权重、CNC 数据集 **全程本地化，不上云**，适合工艺参数/刀具数据等核心资产敏感的企业。

一句话定位：**桌面级制造物理 AI。AI 全流程生成「图纸→3D→工艺→NC」，体素仿真强制校验、DNC 下发硬闸兜底；推理与数据全程本地，工艺资产不出厂。**（口径依据：《产品叙事与战略对标-2026-09》§1.3，对外材料更新前须过 §1.7 叙事↔资产对照表）

---

## 2. 技术栈（Tech Stack）

| 层 | 技术 | 版本/说明 |
|----|------|-----------|
| **前端** | Vue 3 + TypeScript + Vite | Vue 3.4 / TS 5.3 / Vite 6 |
| | 状态/路由/UI | Pinia 2.1（+pinia-plugin-persistedstate）、vue-router 4、Element Plus 2.4、vue-i18n 9 |
| | 3D / 图表 | Three.js 0.170、ECharts 5.5 |
| | 通信 | axios（HTTP/SSE，默认 `localhost:8765`）、`@tauri-apps/api` 2.10 |
| **后端** | Python ≥3.10 + FastAPI | FastAPI 0.115、uvicorn 0.32、Pydantic 2.9、pydantic-settings |
| | 持久化 | SQLAlchemy 2.0 + Alembic（SQLite 主库）、ChromaDB 0.6（向量库）、Redis 5.2（可选缓存/任务标志） |
| | AI 推理 | **ONNX Runtime 1.20**（生产侧 LNN 推理，已替代 torch）、sentence-transformers 3.3 / transformers 4.48、scikit-learn 1.6、Pillow、matplotlib |
| | 工程算法 | numpy 2.1、slowapi（限流） |
| **AI 内核** | LNN（LTC/CFC/Hybrid）、多 LLM Gateway（Ollama/LM Studio/llama.cpp/vLLM/云 API）、RAG 混合检索、知识图谱、刀具磨损预测（PHM2010 + 自采 6061-T6） |
| **桌面壳** | Tauri 2.1（Rust） | sidecar 进程托管 + IPC 命令 + 系统集成（**自动更新未配置**：无 updater 插件/endpoint） |
| **Rust 计算** | `rust/compute` crate | compute-core（纯 Rust 体素切削仿真）+ PyO3 绑定（maturin 编译为 Python 扩展） |
| **Agent 网关** | `mcp_server`（Python） | MCP（FastMCP）暴露 LNN 工具，stdio / SSE 双模式 |
| **工程化** | Docker（docker-compose + Dockerfile）、Git LFS、GitHub Actions（11 工作流）、Husky + commitlint + lint-staged + ruff/black + eslint/prettier |
| **数据存储** | SQLite（结构化：加工/刀具/训练）、ChromaDB（嵌入）、Redis（缓存，可选）、Git LFS（PyTorch 权重） |

> **关键解耦（V2.7.0）**：训练侧依赖（torch/torchdiffeq/mlflow/xgboost）已迁移至 `research/requirements.txt`；LNN 推理侧仅用 ONNX Runtime 消费模型（无 torch）。注意口径：RAG 嵌入（sentence-transformers/transformers）仍硬依赖 CPU-only torch（≈476MB，须随桌面运行时分发，见 `engineering/python/requirements.txt`）；切削力/颤振预测的降级路径为 Kienzle/T-lusty 解析解（非 ONNX）。**随包权重（2026-09 更新）**：`cutting_force` 与 `wear_prediction` 已由训练管线产出真实权重（`engineering/python/models/lnn/`，v2 npz + manifest，见 `models/README.md`）；`surface_roughness`/`temperature` 数据不足未训练，运行时以 `weights_source=random_init` 显式标记；精度口径见 `engineering/python/models/lnn/*.manifest.json` 与 `docs/development/lnn-权重训练与分发接线方案.md` 执行记录。

---

## 3. 目录结构与职责（Directory Map）

### 3.1 顶层关键目录

| 路径 | 角色 |
|------|------|
| `engineering/` | **前端 + 后端同仓根**（原 README 的 `src/` 与 `python/` 已并入此处） |
| `engineering/src/` | Vue 3 前端源码 |
| `engineering/python/app/` | **FastAPI 后端**（66 个一级业务模块，实测 `ls -d app/*/`） |
| `engineering/python/` | 后端运行配置、requirements、sidecar 启动脚本 `sidecar_main.py` |
| `engineering/src-tauri/` | Tauri 2 桌面壳（Rust）：`commands.rs` / `sidecar.rs` / `main.rs` / `lib.rs`（含 `tauri.conf.json`，唯一可构建副本） |
| `rust/compute/` | Rust 计算 crate（体素切削仿真，PyO3 暴露给 Python） |
| `mcp_server/` | Agent Gateway：把 LNN 能力包装为 MCP 工具 |
| `config/` | 运行时 YAML：`data_pipeline.yaml`、`postprocessor_config.yaml`、`safety_rules.yaml` |
| `models/` | LNN 权重分发目录（2026-09 起真实权重随包：cutting_force + wear_prediction，v2 npz + manifest；`engineering/python/models/lnn/`，训练复现见 `models/README.md`） |
| `docs/`、`docs-site/` | 文档体系（20+ 子目录）、VitePress 文档站 |
| `tests/`、`engineering/python/app/benchmarks/` | Vitest 前端测试 + pytest 全套 + 性能基准 |

> **README 与实际布局的差异（重要）**：README 中的 `python/app/...` 与 `src/` 是旧描述，实际路径为 `engineering/python/app/...` 与 `engineering/src/`。V2.7 重构把前端与后端统一收纳到 `engineering/`，并新增 `rust/compute`、`mcp_server/`、`config/`（`shared/` 共享库已在后续重构中移除，其内容并入 engineering 侧）。阅读源码请以磁盘实际结构为准。

### 3.2 后端模块（`engineering/python/app/`）核心职责

| 域 | 关键模块 | 职责 |
|----|----------|------|
| **AI 内核** | `ai/llm/providers/` | 9+ LLM 后端统一网关（云：Claude/DeepSeek/Gemini/OpenAI/Qwen；本地：Ollama/LM Studio/llama.cpp/vLLM/TGI），软依赖 + 规则回退 |
| | `ai/lnn/` | LTC/CFC/Hybrid 的训练/推理/量化/路由（`router` 按任务复杂度选轻量/重型模型） |
| | `ai/process_explainer`、`ai/process_understanding`、`ai/unified_embedding` | AI 决策可解释化、任务分类+知识检索+方案生成、多源统一嵌入空间 |
| **CAM 制造链** | `dxf/`、`step_import/`、`cad/`、`cadquery/`、`parametric_geometry/`、`image_to_3d/`、`cad/nl2cad_llm.py`（另有 `api/v1/nl2cad/`，无 `app/nl2cad/`） | 图纸/三维导入、参数化几何、图生 3D、自然语言→CAD |
| | `feature_extraction/`、`process_planning/` | 特征识别（孔/腔/凸台/型腔）、装夹/工序排序/物理约束验证 |
| | `postprocessor/` | **9 种内置 CNC 后处理器**（Fanuc/Siemens/Heidenhain/Mitsubishi/Fagor/GSK/HNC/KND/xmachine），基于后处理 DSL + 控制器语法树（另有 YAML 方言包变体） |
| | `simulation/`（chatter/cutting_force/kinematics/voxel_cutter） | 颤振稳定性叶瓣图 + LNN 时序预测、切削力、**程序级运动学校验**（3 轴行程/主轴/进给/快移扎刀/装刀，K001-K009，DNC 闸门条件）、体素切削仿真（voxel 调用 Rust compute-core）；多轴 RTCP 逆解与刀柄/夹具干涉仿真未实现 |
| | `gcode_generation/`、`toolpath/` | NC 代码生成；`toolpath/planar_engine.py` 提供 2.5D 几何刀轨层（挖槽环切/外形偏置/端面 raster、Z 分层、斜坡下刀），铣削 G 代码从真实轮廓计算刀心轨迹，几何不可用时回退模板走线 |
| **工业集成** | `dnc/`、`integrations/`（mtconnect/opcua/mes） | 统一 adapter 抽象，多协议并发对接车间设备/MES |
| **知识层** | `rag/`（BM25+向量+RRF+Cross-Encoder 重排）、`knowledge_graph/`（LLM/PDF 抽取+校验+查询） | 混合检索 + 实体关系图谱 |
| **智能体/插件** | `agent/`（auth/middleware/audit/gateway）、`plugins/`（rl_agent/skill_loader/skill_marketplace/world_model/workflow_templates） | 多智能体系统、RL 智能体、技能编译器（RestrictedPython + AST 审计）、技能市场、世界模型 |
| **数据与治理** | `database/`（models/repository，SQLite+ChromaDB）、`auth/`（Bearer+RBAC 4 级 R/W/T/A）、`core/`（logging/exception/config/request_id）、`middleware_stack/` | 持久化、鉴权、配置、中间件链（6 个，含 CORS 安全校验、限流、空闲自动关机） |
| **服务与运维** | `services/`（tool_wear 磨损预测、explainability、project_sync）、`tasks/`（AsyncTaskManager）、`sidecar/`（生命周期/优雅关闭）、`benchmarks/`（api/business/concurrency/database/lnn 推理） | 业务服务、异步任务、桌面 sidecar 编排、性能基准 |
| **API 层** | `api/v1/`（**86 个 APIRouter 文件 / 90 处实例化**，实测 grep `APIRouter(`）、`router_registry.py`、`api/routers/` | 按域分组注册路由（LNN/RAG/模拟/CAM/DNC/MES/插件/Agent…），条件路由失败仅告警不阻断启动 |

### 3.3 前端（`engineering/src/`）

- **26 个视图**（`src/views/*.vue` 实测）：Home、Workspace、ProcessPlanning、Simulation、AgentDashboard、NLModeling、QualityInspection、PluginMarket、TaskBoard、BranchManager、Settings、About 等；
- **组件**（`components/`）：CommandPalette、Copilot、Onboarding、settings（LLM 引擎/路由/AutoDetect）、toolpath-editor、simulation、dxf/step_import、plugin、goals、rule_editor、nl2cad；
- **Pinia stores**（`stores/`）：agents/auth/llmProviders/plugin/processUnderstanding/project/rules…；
- **composables**：useHealthMonitor、useSovereigntySettings（数据主权监控）、useCommandPalette、useSimulationVisualization；
- **i18n**（`locales/`）：zh-CN / en；`api/`：axios 客户端；`contracts/`、`types/`：前后端共享 TS 契约。

### 3.4 Rust 侧

- `engineering/src-tauri/src/sidecar.rs`：启动 Python 后端子进程、轮询 `/api/health/ping` 就绪、退出时先 `POST /api/v1/admin/shutdown` 触发优雅关闭（最多等 8s，超时再 kill），避免 SQLite WAL 未 checkpoint / 文件句柄锁定。
- `engineering/src-tauri/src/commands.rs`：暴露原生命令（如 `close_splashscreen`、dialog、shell）。
- `rust/compute/crates/core/`：纯 Rust 体素切削仿真（`cutting.rs`/`tool.rs`/`voxel_grid.rs`）；`crates/pyo3_bindings/`：编译为 `cdylib` 供 Python `import` 调用（性能关键路径用 Rust 替代纯 Python）。

### 3.5 MCP Agent Gateway（`mcp_server/`）

- `server.py`：`FastMCP` 服务，stdio（本地 MCP 客户端）或 SSE（远程）双模式；默认绑定 `127.0.0.1`；远程暴露需 `LNN_MCP_ALLOW_REMOTE=1` + 强入站令牌 `LINGJING_MCP_INGRESS_TOKEN`（fail-closed，无令牌即拒绝）。
- `tools.py`：通过 HTTP + Bearer Token 调用后端，聚合注册 **26 个工具**（LNN 推理训练 / G 代码任务 / 设备 / 仿真工厂 / CAM 推荐 / DXF 解析 / 工艺规划，分组独立开关）；完整清单与接入示例见 `docs/integrations/mcp-usage.md`；强制 `LINGJING_AGENT_TOKEN ≥ 32` 字符；非回环暴露时要求入站 `LINGJING_MCP_INGRESS_TOKEN` Bearer 鉴权（纯 ASGI 中间件，`hmac.compare_digest` 防时序），生产环境建议经 HTTPS 反向代理暴露。

---

## 4. 数据流向（Data Flow）

```
┌──────────────────────────────────────────────────────────────────┐
│  Vue 3 前端 (engineering/src)  ──HTTP/SSE + Bearer──▶  FastAPI 后端 │
│  Pinia stores / Three.js 3D / ECharts            (engineering/    │
│                                                  python/app :8765) │
└───────────────────────────────┬──────────────────────────────────┘
                                 │
        Tauri Rust 壳 (engineering/src-tauri) 托管 Python 为 sidecar 子进程：
        启动→健康检查(/api/health/ping)→退出→优雅关闭(/api/v1/admin/shutdown)
                                 │
        ┌────────────────────────▼─────────────────────────────┐
        │  后端域服务                                            │
        │  图纸(DXF/STEP)→特征识别→工艺规划→后处理(11控制器)      │
        │        →仿真(颤振/切削力/体素)→DNC(MTConnect/OPC/MES)   │
        │  AI 内核: llm 网关 / lnn(LTC-CFC, ONNX) / rag / 图谱    │
        │  性能关键仿真调用 Rust compute-core (PyO3)              │
        └────────────────────────┬─────────────────────────────┘
                                 │
        ┌────────────────────────▼─────────────────────────────┐
        │  持久化层（全本地）                                    │
        │  SQLite(结构化) · ChromaDB(向量) · Redis(缓存,可选)    │
        │  Git LFS(PyTorch 权重) · 加工数据集(PHM2010/6061-T6)  │
        └───────────────────────────────────────────────────────┘

外部 AI Agent ──MCP(stdio/SSE)──▶ mcp_server ──HTTP+Bearer──▶ 后端（LNN 工具）
```

---

## 5. 核心模块职责速查（Core Modules）

| 模块 | 职责 | 技术底座 |
|------|------|----------|
| **LNN 引擎** | 切削颤振时序预测（LTC/CFC/Hybrid，训练/推理/量化/任务路由） | ONNX Runtime + Rust 加速 |
| **NC 后处理** | 9 种内置控制器 G 代码生成（另支持 YAML 方言包变体） | 后处理 DSL + 控制器语法树 |
| **工艺规划** | 特征识别 + 装夹/工序/物理验证 | 数学规划 + LLM 工艺理解 |
| **仿真引擎** | 颤振叶瓣图、切削力、体素切削 | Three.js + Rust compute-core |
| **RAG + 知识图谱** | 混合检索 + 实体关系抽取/查询 | BM25+向量+RRF+Cross-Encoder 重排 |
| **多 LLM 网关** | 9+ 后端统一接口 + 软依赖回退 | Ollama/LM Studio/llama.cpp/vLLM/云 API |
| **DNC 集成** | 车间设备/MES 对接 | MTConnect/OPC UA/MES 统一 adapter |
| **Agent + 插件** | 多智能体、技能编译器、技能市场、世界模型 | FastAPI + RestrictedPython + AST 审计 |
| **桌面壳** | 单文件分发、sidecar 生命周期、系统集成 | Tauri 2（Rust） |

---

## 6. 当前开发状态（Status）

- **版本与分支**：V2.8.0（2026-09-05 发布），当前分支 `main`（2026-08-19 分支收敛：refactor 分支已并入 main，旧 main 存档于 tag `backup/main-2026-08-03`；历史合并点 `592aedb` V2.7.0 解耦重构、`27b9c2a` V2.6.0 架构重构与契约层建设）。版本演进见根 `CHANGELOG.md`。
- **成熟度**：功能面已较完整（Roadmap 中 LNN、11 后处理器、DNC 适配、RAG、知识图谱、Tauri 打包均已交付）；代码质量处于持续改进中（V2.7 静态审查评定 C 级，核心架构项——单例→DI 迁移、分层整理、前端 API 层激活——仍在分阶段推进中）。
- **进行中的重构（REFACTOR_PLAN_V2.6.1，2026-07-20；V2.8.0 已消化大半）**：
  - 已修：XSS（`ExampleGallery.vue` 三层防御）、`skill_compiler` 降级路径补 AST 审计、`logging_config` 自测守卫、UTC 时区统一。
  - V2.8.0 已拆分：前端巨型组件（Simulation/TaskBoard/Workspace 等，当前最大视图约 18KB）；>40KB Python 巨型文件已拆 4 个（仅剩 `agent/orchestrator.py` 71.8KB / 1,535 行，仍在活跃演进）。
  - 剩余：`agent/orchestrator.py` 拆分、30–40KB 档约 24 个文件的分层整理、29 处裸 `except...pass` 补日志（AST 审查确认原 ~533 处静默 catch 中绝大多数为正确的 asyncio/logging 惯用法）。
- **安全加固**：CORS 启动期强制校验（通配符+凭据即非零退出）、Bearer 鉴权 + RBAC 4 级、MCP Token 强度校验与入站 Bearer 鉴权（`LINGJING_MCP_INGRESS_TOKEN`，`hmac` 防时序攻击）、OPC UA 缺省拒绝匿名（需 `LNN_OPCUA_ALLOW_ANON=1` 显式允许）+ 安全策略强制（默认 `Basic256Sha256`，需 `LNN_OPCUA_ALLOW_NOSECURITY=1` 才降级）、生产环境关闭 `/docs`/`/redoc`/`/openapi`、空闲自动关机、sidecar 优雅关闭。
- **测试与基准**：pytest 全套（`.coverage` 覆盖率数据在）、Vitest 前端、7 类性能基准（api/business/concurrency/database/drawing_parse/lnn_inference/nc_generation）。
- **待办（Roadmap）**：实时颤振在线监测插件、工艺数字孪生、多语言 UI（英/日/德）、移动端工艺看板。

---

## 7. 快速上手（Quick Start）

```bash
# 前端开发（默认 http://localhost:1420）
cd engineering && pnpm install && pnpm dev

# 后端（默认 http://localhost:8765）
cd engineering/python && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8765

# 桌面壳（需先起前后端）
cd engineering && pnpm tauri dev

# 生产构建
cd engineering && pnpm build && pnpm tauri build

# MCP Agent Gateway（可选）
cd mcp_server && LINGJING_AGENT_TOKEN=<≥32字符> python server.py --transport stdio
```

> 注：需在 `.env` / 环境变量设置 `LNN_TOKEN`（Bearer 鉴权，推荐环境变量）、`LINGJING_AGENT_TOKEN`（MCP）。模型权重经 Git LFS 管理，克隆后需 `git lfs pull`。
