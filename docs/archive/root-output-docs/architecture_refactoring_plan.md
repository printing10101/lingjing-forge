# 灵境制造 V2.7.0 → V3.0.0 架构重构方案

**文档日期**：2026-08-01  
**分析范围**：Python 后端 (797 文件)、Vue3 前端 (30 视图/21 Store)、共享契约层、跨层依赖  
**方法论**：基于 SOLID 原则、清洁架构 (Clean Architecture)、DDD 战术模式对三层代码库进行系统分析

---

## 一、当前架构诊断——三层视角

### 1.1 Python 后端

```
                            main.py
                     ┌─────────┼──────────┐
                     v         v          v
            ┌──── API层 (408 次跨模块导入) ────┐
            │  api/v1/  (95 文件, 扁平)          │
            │  → 直接导入 database/models/schemas │
            │  → 19 处违反分层架构               │
            └──────────────┬─────────────────────┘
                           │ ← 14 对循环依赖
            ┌──────────────┼────── 域层 ──────────┐
            │ services/  │ service/  │ agent/    │
            │  (基础设施)  │  (CRUD)   │  (3文件)   │
            │  ← 两个同名包、职责重叠、无分界线        │
            └──────────────┬─────────────────────┘
                           │
            ┌──────────────┼── 基础设施层 ──────────┐
            │ core (148次导入) │ config (127次导入)   │
            │ ← GOD MODULES   │ ← 22处直接引用全局单例  │
            │ database (84)   │ contracts (77)       │
            └───────────────────────────────────────┘
```

**核心矛盾**：API 层与域层之间没有"应用层"（Use Cases/Handlers），导致 API 路由直接编排业务逻辑。

### 1.2 Vue3 前端

```
┌──────────── 视图层 (30 页面) ────────────────┐
│  → 17 个视图直接 import http (绕过 Store/API) │
│  → 3 个超巨型组件 (>1000行)                   │
└──────────────────────┬───────────────────────┘
                       │
┌────────── 状态管理层 (21 Store) ─────────────┐
│  → Store 间零交叉依赖 (孤岛)                   │
│  → 6 个 Store 各自复制 unwrap/分页逻辑         │
│  → plugin.ts 使用 Options API (风格不一致)     │
└──────────────────────┬───────────────────────┘
                       │
┌── API/Composable ──┬── 基础设施层 ───────────┐
│  → 4 个 API 模块    │  → 命名混乱 (3种风格)    │
│  → 12 个 Composable │  → 缺失 barrel exports │
└────────────────────┴─────────────────────────┘
```

**核心矛盾**：Store 层与视图层之间缺少"服务层"抽象——API 调用逻辑散落在 Store、Composable 和视图组件中。

### 1.3 跨层关系

```
shared/          ← 零依赖契约层（设计良好，但无代码 import）
research/        ← 训练栈 (PyTorch)
engineering/     ← 生产栈 (ONNX Runtime) ──(直接 import, type:ignore)──> research/
mcp_server/      ← HTTP 网关 ──(HTTP)──> engineering
```

**核心矛盾**：`shared/` 是一个"未被消费的僵尸契约层"——16 个精心设计的 dataclass/Protocol 没有任何代码实际引用。`engineering/` 绕过 `shared/` 直接导入 `research/`（8 处，全部标注 `type:ignore`）。

---

## 二、架构问题总览

| 严重度 | 数量 | 描述 |
|:------:|:---:|------|
| **严重** | 5 | services/service 重叠、14对循环依赖、API直接导入DB、shared未被消费、engineering导入research |
| **高** | 6 | core/config GOD MODULE、60+全局单例、非API包含路由、17视图绕过Store、组件放错目录、超大文件 |
| **中** | 8 | 命名不一致、重复代码、缺失barrel、类型反向引用、mcp_server不引用shared、Store间通信缺失 |
| **低** | 5 | 测试目录分散、Options API残留、路由守卫不一致、懒加载缺失、魔法数字 |

---

## 三、目标架构——V3.0.0 的设计

### 3.1 后端目标架构：清洁架构 (Clean Architecture)

```
┌──────────────────────────────────────────────────────────────┐
│                   api/  (HTTP 适配器)                         │
│  routers/     ← 仅路由定义 + 请求/响应模型                     │
│  middleware/  ← 认证/限流/CORS/日志                           │
│  dependencies/ ← FastAPI Depends 组装                         │
└─────────────────────┬────────────────────────────────────────┘
                      │  依赖方向 ↓
┌─────────────────────┼────────────────────────────────────────┐
│              application/  (应用层 / 用例)                     │
│  handlers/    ← 用例编排 (CreateProject, RunSimulation...)     │
│  ports/       ← 输入端口 (命令/查询接口)                        │
│  dto/         ← 数据传输对象                                   │
└─────────────────────┬────────────────────────────────────────┘
                      │  依赖方向 ↓
┌─────────────────────┼────────────────────────────────────────┐
│               domain/  (领域层)                               │
│  entities/    ← 核心业务实体 (Project, Simulation, Workflow...)│
│  services/    ← 领域服务 (ChatterPredictor, ToolPathPlanner...)│
│  repositories/ ← 仓库接口 (IProjectRepo, ISimulationRepo...) │
│  events/      ← 领域事件                                       │
└─────────────────────┬────────────────────────────────────────┘
                      │  依赖方向 ↓
┌─────────────────────┴────────────────────────────────────────┐
│            infrastructure/  (基础设施层)                       │
│  database/    ← ORM 模型 + SQL 仓库实现                        │
│  redis/       ← Redis 缓存实现                                 │
│  tdengine/    ← 时序数据库实现                                  │
│  ai/llm/      ← LLM Provider 实现                              │
│  config/      ← 配置管理                                       │
└──────────────────────────────────────────────────────────────┘
```

**关键原则**：
- 依赖方向：api → application → domain ← infrastructure（依赖反转）
- domain 层零外部依赖（不依赖 FastAPI/SQLAlchemy/Redis）
- application 层仅依赖 domain 的接口（端口-适配器模式）
- infrastructure 层实现 domain 定义的接口

### 3.2 前端目标架构：Feature-Sliced Design 简化版

```
┌────────────────────────────────────────────────────────────┐
│                    pages/  (页面层)                          │
│  home/ workspace/ simulation/ task-board/ workflow-panel/  │
│  → 每个 page 是自治文件夹（含自己的 components/composables）  │
└────────────────────┬───────────────────────────────────────┘
                     │
┌────────────────────┼───────────────────────────────────────┐
│               features/  (功能模块)                          │
│  nl2cad/ dxf-import/ step-import/ toolpath-editor/         │
│  → 跨页面共享的功能模块，每个含 store + api + types           │
└────────────────────┬───────────────────────────────────────┘
                     │
┌────────────────────┼───────────────────────────────────────┐
│              shared/  (共享基础设施)                          │
│  api/       ← HTTP 客户端 + 拦截器                           │
│  stores/    ← 全局 Store (auth, settings, version)          │
│  ui/        ← 通用 UI 组件 (AppLayout, ErrorBoundary...)    │
│  utils/     ← 纯工具函数                                     │
│  types/     ← 前端专用类型                                    │
│  contracts/ ← 前后端共享契约 (只读)                           │
└────────────────────────────────────────────────────────────┘
```

**关键原则**：
- 页面层可以导入 features/ 和 shared/
- features/ 可以导入 shared/，不可相互导入
- shared/ 只依赖第三方库，不依赖项目代码

### 3.3 跨层目标架构

```
┌─────────────────────────────────────────────────────────┐
│  shared/  ← 强制执行契约                                  │
│  ├── models/     ← API DTO (Pydantic)                   │
│  ├── events/     ← 领域事件 (dataclass)                   │
│  ├── errors/     ← 错误码 + 异常类型                       │
│  └── contracts/  ← 仓库接口 (Protocol)                    │
│                                                         │
│  被以下消费: engineering/domain/  , mcp_server/          │
│                research/training/ , engineering/frontend/ │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  research/  ← 纯训练栈                                    │
│  ├── 消费 shared/ 的 DatasetSpec, ModelCard               │
│  ├── 产出: ONNX 模型 → models/ 目录                       │
│  └── 不导入 engineering/, 零耦合                          │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  engineering/  ← 生产栈                                   │
│  ├── domain/  ← 消费 shared/ 契约                         │
│  ├── infrastructure/ai/lnn/  ← 加载 ONNX (不是 PyTorch)   │
│  └── 不导入 research/ (通过模型文件解耦)                    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  mcp_server/  ← Agent Gateway                            │
│  ├── 消费 shared/models/ 进行 I/O 校验                    │
│  └── 通过 HTTP 调用 engineering/，不导入其代码             │
└─────────────────────────────────────────────────────────┘
```

---

## 四、分阶段重构方案

### 阶段 0：基础安全（第 1 周）—— 不改变功能，只消除风险

**目标**：修复已发现的 36 项代码质量问题（三轮审查已完成），为后续重构清理跑道。

**已完成的修复**（三轮代码审查）：
- `@safe_endpoint` 装饰器：消除 ~500 行重复错误处理
- SQL 列名白名单校验模块
- MCP 输入验证（5 函数）
- TypeScript 严格模式配置
- CSP 加固（Web + Tauri 两端）
- K8s NetworkPolicy 最小权限
- 5 处 `:key="index"` 反模式修复
- 部署配置安全修复（Nginx/TDengine/CI/GitHub Actions）

**本阶段额外行动**：
- [ ] 确认所有 `.pyc` 文件被 `.gitignore` 排除
- [ ] 运行 `ruff check --fix` 全量自动修复
- [ ] 确认 CI 中 `vue-tsc --noEmit` 通过（严格模式生效）

**风险评估**：零功能风险，纯质量修复。

---

### 阶段 1：消除循环依赖（第 2-3 周）—— 解除架构锁

**目标**：消除 14 对循环依赖，解除重构的"先有鸡还是先有蛋"锁死。

**1.1 API 层路由归位**（解决 api<->simulation/rag/dxf/projects/rules/sharp/plugin 8 对循环依赖）

当前问题：`simulation/api.py`、`rag/routes.py`、`cad/process_router.py`、`dxf/api.py` 等非 API 包内定义了 FastAPI 路由。

迁移方式：
```python
# 旧代码 (simulation/api.py)
from fastapi import APIRouter
router = APIRouter()
@router.post("/simulate")
async def simulate(...): ...

# 新代码 (api/v1/simulation/routes.py)
from fastapi import APIRouter
from app.simulation.use_cases import SimulateUseCase
router = APIRouter()
@router.post("/simulate")
async def simulate(...):
    return await SimulateUseCase(...).execute()
```

**待迁移路由**：
| 源位置 | 目标位置 | 影响端点 |
|--------|---------|---------|
| `simulation/api.py` | `api/v1/simulation/` | ~8 端点 |
| `rag/routes.py` | `api/v1/rag/` | ~6 端点 |
| `cad/process_router.py` | `api/v1/cad/` | ~5 端点 |
| `dxf/api.py` | `api/v1/dxf/` | ~4 端点 |
| `ai/ollama_routes.py` | `api/v1/ai/` | ~3 端点 |
| `integrations/mes/api.py` | `api/v1/mes/` | ~3 端点 |
| `knowledge_graph/extractor/review.py` | `api/v1/knowledge-graph/` | ~2 端点 |

**1.2 引入 contracts 解决 domain 层循环依赖**

对 `agent<->auth`, `database<->knowledge_graph`, `ai<->rag` 等 domain 内部循环：

```python
# contracts/authentication.py
class IAuthProvider(Protocol):
    def check_permission(self, user_id: str, resource: str) -> bool: ...

# agent/orchestrator.py (依赖抽象)
from app.contracts.authentication import IAuthProvider

# auth/permissions.py (实现抽象)
class DefaultAuthProvider(IAuthProvider): ...
```

**验证标准**：`grep -r "from app\." | sort | uniq -c | sort -rn` 输出不再包含任何循环对。

**风险评估**：中等。路由迁移是纯机械操作，但端点 URL 必须保持不变（`@router.post("/simulate")` 路径不变）。需要完整回归测试覆盖。

---

### 阶段 2：建立清洁架构基础（第 4-6 周）—— 分层重构

**目标**：建立 `domain/` → `application/` → `infrastructure/` 分层架构，先建骨架再搬代码。

**2.1 合并 services/ 和 service/**

```python
# 新结构
app/
  domain/          # 新：领域层
    entities/      # Project, Simulation, Task, Goal, Rule...
    services/      # 领域服务 (合并后的业务逻辑)
    events/        # 领域事件
  application/     # 新：应用层
    handlers/      # 用例编排
    ports/         # 接口定义
  infrastructure/  # 重组：基础设施
    persistence/   # ← 原 database/
    cache/         # ← 原 services/redis_client.py
    llm/           # ← 原 ai/llm/
    config/        # ← 原 config/
```

合并策略：
- `services/` 中的基础设施 → `infrastructure/` (Redis, Memory, TDengine)
- `services/` 中的业务逻辑 → `domain/services/` (RL Agent, World Model, Explainability)
- `service/` 中的 CRUD → `domain/services/` (Documents, Equipment, Materials)

**2.2 引入 Repository 模式**

```python
# domain/repositories.py (纯接口，零依赖)
class IProjectRepository(Protocol):
    async def get(self, project_id: str) -> Project: ...
    async def save(self, project: Project) -> None: ...
    async def list(self, filters: ProjectFilters) -> list[Project]: ...

# infrastructure/persistence/project_repo.py (SQLite 实现)
class SQLiteProjectRepository(IProjectRepository):
    def __init__(self, db: sqlite3.Connection): ...
    async def get(self, project_id: str) -> Project: ...
```

**2.3 用 FastAPI Depends 替代全局单例**

```python
# 旧代码 (全局单例)
global _orchestrator
def get_orchestrator() -> AgentOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AgentOrchestrator(config)
    return _orchestrator

# 新代码 (FastAPI Depends)
async def get_orchestrator(
    config: AppConfig = Depends(get_config),
) -> AgentOrchestrator:
    return AgentOrchestrator(config)

@router.post("/run-agent")
async def run_agent(
    orchestrator: AgentOrchestrator = Depends(get_orchestrator),
): ...
```

预计替换 60+ 个 `get_xxx()` 全局单例。

**2.4 拆分超大文件**

目标：每个文件 <500 行。

| 文件 | 当前行数 | 拆分方案 |
|------|:---:|------|
| `cam_validation/routes.py` | 1473 | → handlers/ + validators/ + schemas/ |
| `gcode_generator.py` | 1289 | → domain/entities/gcode.py + domain/services/gcode_generator.py |
| `simulation/api.py` | 1265 | → 已由步骤 1.1 处理 |
| `state_persistence.py` | 1194 | → domain/repositories.py + infrastructure/persistence/state/ |
| `rag_retrieval.py` | 1165 | → domain/services/retrieval.py + infrastructure/vector_store/ |

**风险评估**：高。这是最大的一次性变更。必须使用"绞杀者模式"（Strangler Fig）——新旧代码并行运行，逐步切换。

---

### 阶段 3：激活 shared/ 契约层（第 7-8 周）—— 跨层解耦

**目标**：让 `shared/` 从"文档"变成"可执行契约"。

**3.1 让 engineering 消费 shared/**

```python
# engineering/domain/entities/chatter.py
from shared.lnn.types import FeatureChatterResult  # ← 新增 import

class ChatterPredictionService:
    def predict(self, features: CuttingFeatures) -> FeatureChatterResult:
        result = self.model.predict(features.to_vector())
        return FeatureChatterResult(
            probability=result.prob,
            severity=result.severity,
            ...
        )
```

**3.2 让 research 消费 shared/**

```python
# research/training/dataset_cache.py
from shared.data.dataset import DatasetSpec  # ← 新增 import

def create_dataset(spec: DatasetSpec) -> Dataset: ...
```

**3.3 消除 engineering → research 直接导入**

8 处 `type:ignore` 导入需要逐一处理：

| 文件 | 当前导入 | 解决方案 |
|------|---------|---------|
| `simulation/chatter/predictor.py` | `research.models.torch_ltc_model` | 通过 ONNX Runtime 加载，不导入 PyTorch |
| `agent_gateway/training.py` | `research.training.trainer` | 训练端点改为通过消息队列触发 research-worker |
| `cutting_force/trainer.py` | `research.training.reproducibility` | 将 `reproducibility` 模块移到 shared/ |
| `tasks/execution.py` | `research.training.trainer` | 同 agent_gateway 方案 |
| `dxf/process_service.py` | `research.multimodal_jepa` | 将 chamfer_heuristic 提取到 shared/ |
| `dreaming/session_extractor.py` | `research.training.experiment_tracker` | 将 tracker 接口移到 shared/ |
| `plugins/world_model/.../fusion_trainer.py` | `research.training.*` | 通过 shared/ 接口解耦 |

**风险评估**：中等。ONNX 模型加载已部分实现（注释中提到），需要验证 ONNX 模型可用。

---

### 阶段 4：前端架构重整（第 9-10 周）—— Feature-Sliced Design

**4.1 目录重组**

```
src/
  pages/
    home/          ← Home.vue + 子组件 + composables
    workspace/     ← Workspace.vue + 子组件
    simulation/    ← Simulation.vue + useSimulation*.ts
    ...
  features/
    dxf-import/    ← DxfImportDialog.vue + store + api + types
    step-import/   ← StepImportDialog.vue + store + api
    nl2cad/        ← NLInputPanel.vue + WorkflowGuide.vue + store + api
    toolpath-editor/ ← ToolpathEditor.vue + store + composables
    copilot/       ← RecommendationCard.vue + store
    ...
  shared/
    stores/        ← auth, settings, version (全局 Store)
    api/           ← http client + interceptors
    ui/            ← AppLayout, ErrorBoundary, SplashScreen
    utils/         ← 工具函数
    types/         ← 前端专用类型
    contracts/     ← 前后端共享契约
```

**4.2 引入服务层——消除视图直接调用 http**

```typescript
// 当前反模式
// views/CostDashboard.vue
const { data } = await http.get('/api/v1/budget/status')

// 目标模式
// features/cost-dashboard/api.ts
export async function getBudgetStatus(): Promise<BudgetStatus> { ... }

// features/cost-dashboard/store.ts
export const useCostStore = defineStore('cost', () => {
  const status = ref<BudgetStatus>()
  async function load() { status.value = await getBudgetStatus() }
  return { status, load }
})

// pages/cost-dashboard/CostDashboard.vue
const store = useCostStore()
onMounted(() => store.load())
```

覆盖 17 个视图组件。

**4.3 统一命名与代码风格**

- 所有目录 → kebab-case
- 所有 Store → Composition API (重写 plugin.ts)
- 提取重复的 `unwrap` + `PaginationState` 到 shared/
- 添加所有模块的 barrel exports (index.ts)

**风险评估**：低（仅代码重组，不改功能）。

---

### 阶段 5：持续改进（第 11-12 周后）

**5.1 引入架构测试**

```python
# tests/architecture/test_layers.py
def test_domain_has_no_fastapi_import():
    """domain/ 层不应导入 FastAPI"""
    assert_no_import("app.domain", "fastapi")

def test_api_does_not_import_database():
    """api/ 层不应直接导入 database/"""
    assert_no_import("app.api", "app.database")

def test_no_circular_imports():
    """整个项目不应存在循环导入"""
    assert_no_circular_imports("app")
```

**5.2 性能基准线**

- 建立 API 端点响应时间基线和 CI 性能回归检测
- 监控重构前后关键端点（simulate, predict, train）的 P50/P95 延迟

**5.3 文档同步**

- 更新 `PROJECT_OVERVIEW.md` 反映新架构
- 为 domain/ 层编写 ADR（架构决策记录）

---

## 五、风险矩阵

| 阶段 | 风险等级 | 主要风险 | 缓解措施 |
|:---:|:---:|------|------|
| 0 | 零 | — | 仅修复已审查的代码问题 |
| 1 | 中 | 路由迁移可能改变端点路径 | 保持 URL 不变；全量回归测试 |
| 2 | **高** | 分层重构可能引入 Bug | 绞杀者模式：新旧并行；分模块逐步切换 |
| 3 | 中 | ONNX 模型兼容性 | 先在 staging 环境验证所有模型可加载 |
| 4 | 低 | 目录重组可能破坏导入 | `tsconfig.json` 路径别名自动重定向 |
| 5 | 低 | 架构测试可能误报 | 允许合理的 `# noqa` 豁免 |

---

## 六、成功标准

### 定量指标

| 指标 | 当前 | 目标 |
|------|:---:|:---:|
| 循环依赖对数 | 14 | 0 |
| API 直接导入 DB 数 | 19 | 0 |
| 全局单例数 | 60+ | 0 |
| engineering → research 导入 | 8 | 0 |
| shared/ 被引用文件数 | 0 | ≥10 |
| 超大文件 (>1000行) | 9 | 0 |
| 视图直接调用 http | 17 | 0 |
| 端点平均 try/except 行数 | 8 | 0 (装饰器) |

### 定性指标

- `domain/` 层不含任何 `from fastapi` / `from sqlalchemy` import
- `shared/` 层被 engineering/ 和 research/ 各自至少 5 个文件引用
- 新人理解一个新功能的代码无需跨越 5 个以上文件
- 添加新 API 端点只需修改 `api/` + `application/` 两层
- 运行时依赖注入可被 `pytest` 轻松 mock

---

## 七、执行建议

1. **不要一次性重构**：每个阶段独立完成、测试、合并到 main（Feature Flag 保护未完成的路径）
2. **先建立测试安全网**：重构前补充核心链路的集成测试（simulate, predict, train, 项目管理）
3. **每个阶段设定明确的"回滚点"**：如果发现重构引入的 Bug 超过 5 个/阶段，立即回滚
4. **代码审查聚焦架构合规**：重构期间的 PR 审查使用架构 lint 规则自动检查（pytest-arch，dependency-cruiser）
5. **阶段 2 最关键**：分层重构是风险最高的步骤，建议先在独立分支上做 1-2 个模块的"概念验证"（如先重构 auth 模块），验证模式可行后再推广
