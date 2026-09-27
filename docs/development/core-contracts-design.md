# 核心架构契约设计（Core Contracts Design）

**文档状态**: 提议（待 review）
**对应 ADR**: [ADR-005-核心架构契约设计](../adr/ADR-005-核心架构契约设计.md)
**最后更新**: 2026-07-13
**适用版本**: 灵境制造 v2.5.0 → v3.0（生态化演进）

---

## 0. 文档目的与读者

本文档定义灵境制造从"功能堆叠"演进为"生态平台"所需的**五大核心契约**的详细接口。任何后续模块（包括世界模型、RL、第三方插件）都必须长在这套契约之上。

**读者**：
- 项目负责人（review 与拍板）
- 后续模块实现者（按契约编码）
- ADR 评审（变更走 ADR 流程）

**文档边界**：本文只定义契约接口与边界划分，不定义具体实现。实现细节在各模块自己的设计文档中展开。

---

## 1. 契约总览与分层

```
┌─────────────────────────────────────────────────────────────┐
│  业务插件层（LTC / RAG / SHARP / 仿真 / CAM / 世界模型 / RL）  │
├─────────────────────────────────────────────────────────────┤
│  五大核心契约层（本文档定义）                                 │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┐  │
│  │ Task     │ Dataset  │ Plugin   │ Config   │ Observ   │  │
│  │ Workflow │ Version  │ ExtPoint │ Spec     │ Trace    │  │
│  │ DAG      │ Lineage  │          │ YAML     │ Snapshot │  │
│  └──────────┴──────────┴──────────┴──────────┴──────────┘  │
├─────────────────────────────────────────────────────────────┤
│  现有基础设施层（adapter 适配，不重写）                       │
│  AsyncTaskManager / data_lake / plugin_system / config.py /  │
│  MLflow / MetricsCollector / logging_config                  │
├─────────────────────────────────────────────────────────────┤
│  Tauri 2 + Vue 3 桌面壳 / FastAPI 后端 / SQLite + ChromaDB   │
└─────────────────────────────────────────────────────────────┘
```

### 1.1 契约清单

| 契约 | Python 抽象基类 | TypeScript 类型 | 现有基础 | adapter 位置 |
|------|----------------|----------------|---------|-------------|
| 任务 | `app/contracts/task.py` | `src/contracts/task.ts` | `app/tasks/task_system.py` | `app/tasks/contract_adapter.py` |
| 数据 | `app/contracts/dataset.py` | `src/contracts/dataset.ts` | `app/training/data_lake.py` | `app/training/contract_adapter.py` |
| 插件 | `app/contracts/plugin.py` | `src/contracts/plugin.ts` | `app/plugins/plugin_system.py` | `app/plugins/contract_adapter.py` |
| 配置 | `app/contracts/config.py` | `src/contracts/config.ts` | `app/config.py` | `app/config_contract_adapter.py` |
| 可观测 | `app/contracts/observability.py` | `src/contracts/observability.ts` | MLflow + metrics + logging | `app/observability/contract_adapter.py` |

### 1.2 契约稳定性承诺

- 契约接口一经 ADR 评审通过，标记为 `Stable`，**只能向后兼容扩展**
- 任何 breaking change 必须新开 ADR，标注 `Deprecates`，并提供至少一个版本的兼容期
- 契约代码与实现代码分离：契约在 `app/contracts/`，实现在各业务模块
- 契约变更必须同步更新 OpenAPI schema 与 TypeScript 类型，CI 强制校验

---

## 2. 核心 vs 插件边界划分

**核心原则**：核心层只保留"任何业务模块都需要"的能力。业务能力一律插件化。

### 2.1 边界划分表

| 模块/能力 | 归属 | 依据 | 改造动作 |
|----------|------|------|---------|
| 任务执行框架（AsyncTaskManager） | **核心** | 所有业务都需要异步执行 | 升级为契约实现，增加 Workflow 编排层 |
| Workflow DAG 编排 | **核心** | 任何多步骤流程都需要 | 新增 `app/workflow/` |
| 数据集抽象（schema/version/hash） | **核心** | 任何模型训练都需要 | 重写 `data_lake` 为 `Dataset` |
| 数据版本/血缘 | **核心** | 复现性必需 | 新增 `app/data/lineage.py` |
| 插件框架本身 | **核心** | 生态地基 | 升级为契约实现 |
| 插件市场 | **核心** | 分发基础设施 | 保留 `app/plugins/marketplace` |
| 配置系统（YAML/继承/sweep） | **核心** | 实验可复现必需 | 新增 `app/config/spec.py` |
| 可观测（trace/metric/log/snapshot） | **核心** | 任何模块都需要埋点 | 新增 `app/observability/` |
| OpenAPI 自动生成 | **核心** | 契约同步基础设施 | 新增 `app/contracts/openapi_gen.py` |
| IPC / HTTP 层 | **核心** | 前后端通信基础设施 | 保留现有 `useBackendStatus` / `http.ts` |
| LTC 颤振预测 | **插件** | 业务能力 | 改为 `plugins/ltc_chatter/` |
| RAG 检索 | **插件** | 业务能力 | 改为 `plugins/rag_retrieval/` |
| SHARP 三元组验证 | **插件** | 业务能力 | 改为 `plugins/sharp_agent/` |
| 数控加工仿真 | **插件** | 业务能力 | 改为 `plugins/machining_sim/` |
| CAM / 工艺规划 | **插件** | 业务能力 | 改为 `plugins/process_planning/` |
| 工艺理解（NL2CAD） | **插件** | 业务能力 | 改为 `plugins/process_understanding/` |
| 飞轮反馈闭环 | **插件** | 业务能力（依赖核心数据/任务契约） | 改为 `plugins/data_flywheel/` |
| 世界模型 | **插件** | 远期业务能力 | 阶段 8 接入 |
| RL agent | **插件** | 远期业务能力 | 阶段 8 接入 |
| 前端工作区扩展点 | **核心机制** | 生态地基 | 新增 `src/contracts/extension.ts` |
| 前端 TaskBoard / Workflow 面板 | **核心 UI** | 任何任务都需要 | 升级现有 `TaskBoard.vue` |
| 前端 LTC / RAG / 仿真视图 | **业务 UI** | 业务能力 | 作为插件 UI 通过扩展点注入 |

### 2.2 插件能力授权矩阵

插件通过声明 `capabilities` 字段请求核心能力，核心层通过 `CapabilityGatekeeper` 授权：

| 能力名 | 含义 | 默认授权 |
|--------|------|---------|
| `task:submit` | 提交任务 | 是 |
| `task:workflow:run` | 运行 DAG 工作流 | 是 |
| `dataset:read` | 读取数据集 | 是 |
| `dataset:write` | 写入数据集 | 否（需用户确认） |
| `dataset:version:create` | 创建数据集版本 | 否 |
| `config:sweep` | 启动超参搜索 | 否 |
| `observability:snapshot:create` | 创建实验快照 | 是 |
| `observability:trace:export` | 导出 trace | 是 |
| `plugin:install` | 安装其他插件 | 否（需管理员） |
| `compute:gpu` | 使用 GPU | 是（若可用） |
| `network:egress` | 外网访问 | 否（默认本地化） |

---

## 3. 任务契约（Task & Workflow Contract）

### 3.1 设计目标

- 单任务执行能力（现有 AsyncTaskManager 已具备）
- **新增**：DAG 工作流编排（多任务依赖、断点续跑、并行/串行）
- **新增**：任务模板（可复用、可参数化、可分享）
- **新增**：任务输入输出契约化（任务间数据通过 Artifact 传递）

### 3.2 Python 抽象基类

```python
# app/contracts/task.py
"""任务契约：定义任务、工作流、任务模板的统一接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol


class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # 工作流中前置失败时的跳过状态


class TaskPriority(int, Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


@dataclass
class Artifact:
    """任务输入输出产物契约。"""
    name: str
    type: str  # "dataset" / "model" / "report" / "metrics" / "file"
    uri: str  # 内部 URI，如 "dataset://my-ds/v3" / "model://ltc-v1"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskContext:
    """任务运行时上下文，由编排器注入。"""
    job_id: str
    workflow_run_id: Optional[str] = None
    inputs: dict[str, Artifact] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    deadline_ts: Optional[float] = None  # Unix ts，超时自动 CANCELLED


@dataclass
class TaskResult:
    """任务执行结果契约。"""
    status: TaskStatus
    outputs: dict[str, Artifact] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None


class TaskHandler(Protocol):
    """任务处理器协议。插件通过实现此协议注册任务类型。"""
    def name(self) -> str: ...
    def description(self) -> str: ...
    def input_schema(self) -> dict[str, Any]: ...
    def output_schema(self) -> dict[str, Any]: ...
    async def execute(self, ctx: TaskContext) -> TaskResult: ...


class ITaskRegistry(ABC):
    """任务类型注册表契约。"""

    @abstractmethod
    def register(self, handler: TaskHandler, *, plugin_id: str) -> None: ...

    @abstractmethod
    def get(self, task_type: str) -> TaskHandler: ...

    @abstractmethod
    def list(self) -> list[dict[str, Any]]: ...


class ITaskExecutor(ABC):
    """单任务执行器契约（现有 AsyncTaskManager 适配此接口）。"""

    @abstractmethod
    async def submit(
        self,
        task_type: str,
        params: dict[str, Any],
        *,
        owner_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout_seconds: int = 3600,
    ) -> str:
        """返回 job_id。"""

    @abstractmethod
    async def get(self, job_id: str) -> TaskResult: ...

    @abstractmethod
    async def cancel(self, job_id: str) -> bool: ...

    @abstractmethod
    def subscribe(self, job_id: str) -> "AsyncIterator[TaskProgress]": ...


# ---------------------------------------------------------------------------
# Workflow DAG 契约
# ---------------------------------------------------------------------------

@dataclass
class WorkflowNode:
    """DAG 节点：一个任务实例。"""
    node_id: str
    task_type: str
    params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)
    # inputs 形如 {"input_name": "${upstream_node_id.output_name}"}
    retry: int = 0
    timeout_seconds: int = 3600


@dataclass
class WorkflowEdge:
    """DAG 边：依赖关系。"""
    upstream: str  # node_id
    downstream: str  # node_id


@dataclass
class WorkflowSpec:
    """工作流规格契约（可序列化为 YAML 模板）。"""
    name: str
    version: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    inputs: dict[str, Artifact] = field(default_factory=dict)  # 工作流级输入
    outputs: dict[str, str] = field(default_factory=dict)  # 形如 {"out": "${node_id.out}"}
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        """校验 DAG 无环、节点引用合法、输入输出匹配。返回错误列表。"""


class IWorkflowRunner(ABC):
    """工作流执行器契约。"""

    @abstractmethod
    async def run(
        self,
        spec: WorkflowSpec,
        *,
        inputs: Optional[dict[str, Artifact]] = None,
        resume_from: Optional[str] = None,  # workflow_run_id，断点续跑
        owner_id: Optional[str] = None,
    ) -> str:
        """返回 workflow_run_id。"""

    @abstractmethod
    async def get_status(self, workflow_run_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def cancel(self, workflow_run_id: str) -> bool: ...

    @abstractmethod
    def subscribe(self, workflow_run_id: str) -> "AsyncIterator[WorkflowEvent]": ...
```

### 3.3 TypeScript 类型

```typescript
// src/contracts/task.ts

export type TaskStatus =
  | 'pending' | 'queued' | 'running'
  | 'completed' | 'failed' | 'cancelled' | 'skipped';

export type TaskPriority = 1 | 5 | 8 | 10;

export interface Artifact {
  name: string;
  type: 'dataset' | 'model' | 'report' | 'metrics' | 'file';
  uri: string;
  metadata: Record<string, unknown>;
}

export interface TaskContext {
  job_id: string;
  workflow_run_id?: string;
  inputs: Record<string, Artifact>;
  config: Record<string, unknown>;
  retry_count: number;
  deadline_ts?: number;
}

export interface TaskResult {
  status: TaskStatus;
  outputs: Record<string, Artifact>;
  metrics: Record<string, number>;
  error?: string;
  error_code?: string;
}

export interface TaskProgress {
  job_id: string;
  status: TaskStatus;
  progress: number; // 0..1
  message?: string;
  timestamp: number;
}

export interface WorkflowNode {
  node_id: string;
  task_type: string;
  params: Record<string, unknown>;
  inputs: Record<string, string>; // ${upstream.output}
  retry: number;
  timeout_seconds: number;
}

export interface WorkflowSpec {
  name: string;
  version: string;
  nodes: WorkflowNode[];
  edges: { upstream: string; downstream: string }[];
  inputs: Record<string, Artifact>;
  outputs: Record<string, string>;
  metadata: Record<string, unknown>;
}

export interface WorkflowEvent {
  workflow_run_id: string;
  node_id?: string;
  event_type: 'node_started' | 'node_completed' | 'node_failed' | 'workflow_completed';
  payload: TaskResult | TaskProgress;
  timestamp: number;
}
```

### 3.4 现有 AsyncTaskManager adapter 方案

**不重写** AsyncTaskManager。新增 `app/tasks/contract_adapter.py`：

```python
# app/tasks/contract_adapter.py（示意，不是最终实现）
class AsyncTaskManagerContractAdapter(ITaskExecutor):
    """把现有 AsyncTaskManager 适配为任务契约。"""

    def __init__(self, manager: AsyncTaskManager):
        self._mgr = manager

    async def submit(self, task_type, params, **kwargs) -> str:
        # 调用 self._mgr.create_task(...)，把 priority/timeout 翻译为现有参数
        ...

    async def get(self, job_id) -> TaskResult:
        record = self._mgr.get_task(job_id)
        return TaskResult(
            status=TaskStatus(record.status.value),
            outputs=record.result.get('outputs', {}) if record.result else {},
            metrics=record.metrics or {},
            error=record.error,
        )
    ...
```

**改造点**：
1. 新增 `app/workflow/` 目录，实现 `WorkflowRunner`（基于 networkx DAG）
2. 新增 `app/tasks/registry.py`，TaskHandler 注册表，插件通过入口点注册
3. AsyncTaskManager 保持不变，通过 adapter 暴露契约
4. 新增 `workflow_runs` / `workflow_nodes` 数据库表

---

## 4. 数据契约（Dataset & Version & Lineage Contract）

### 4.1 设计目标

- 替换 `data_lake.py` 的"裸 JSONL 追加"
- 引入 schema 化的数据集抽象（Pydantic 模型）
- 版本化（不可变快照 + 增量版本）
- 内容寻址（hash）+ 血缘追踪（哪个任务/工作流产生）
- 与 MLflow 集成：训练时强制记录数据 hash

### 4.2 Python 抽象基类

```python
# app/contracts/dataset.py
"""数据集契约：定义数据集、版本、血缘的统一接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Optional


class DatasetStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"  # 不可变
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


@dataclass
class DatasetSchema:
    """数据集 schema 契约（Pydantic 模型序列化形式）。"""
    fields: dict[str, dict[str, Any]]  # {"column": {"type": "float", "required": True}}
    primary_key: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DatasetVersion:
    """数据集版本契约。"""
    dataset_id: str
    version: str  # semver，如 "1.0.0"
    status: DatasetStatus
    schema: DatasetSchema
    content_hash: str  # sha256，内容寻址
    row_count: int
    size_bytes: int
    created_at: datetime
    created_by: str  # user_id 或 plugin_id
    lineage: Optional[str] = None  # lineage record id
    storage_uri: str  # 实际存储位置


@dataclass
class LineageRecord:
    """血缘记录契约。"""
    record_id: str
    target: str  # "dataset://my-ds/v1" / "model://ltc-v1"
    source_type: str  # "task" / "workflow" / "manual" / "external"
    source_ref: str  # job_id / workflow_run_id / url
    inputs: list[str] = field(default_factory=list)  # 上游 artifact uri
    outputs: list[str] = field(default_factory=list)
    operation: str = ""  # "train" / "preprocess" / "augment"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


class IDatasetStore(ABC):
    """数据集存储契约。"""

    @abstractmethod
    async def create(
        self,
        name: str,
        schema: DatasetSchema,
        *,
        owner_id: str,
        description: str = "",
    ) -> str:
        """返回 dataset_id。"""

    @abstractmethod
    async def commit_version(
        self,
        dataset_id: str,
        records: list[dict[str, Any]],
        *,
        version: Optional[str] = None,  # None 则自动递增 patch
        lineage: Optional[LineageRecord] = None,
    ) -> DatasetVersion:
        """提交一个不可变版本。计算 content_hash，写入存储。"""

    @abstractmethod
    async def get_version(
        self, dataset_id: str, version: Optional[str] = None
    ) -> DatasetVersion:
        """version=None 返回最新 published 版本。"""

    @abstractmethod
    async def read(
        self,
        dataset_id: str,
        version: Optional[str] = None,
        *,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """流式读取。"""

    @abstractmethod
    async def list_versions(self, dataset_id: str) -> list[DatasetVersion]: ...

    @abstractmethod
    async def deprecate(self, dataset_id: str, version: str) -> None: ...


class ILineageStore(ABC):
    """血缘存储契约。"""

    @abstractmethod
    async def record(self, lineage: LineageRecord) -> str: ...

    @abstractmethod
    async def get_upstream(
        self, target_uri: str, *, depth: int = 10
    ) -> list[LineageRecord]: ...

    @abstractmethod
    async def get_downstream(
        self, target_uri: str, *, depth: int = 10
    ) -> list[LineageRecord]: ...

    @abstractmethod
    async def visualize(self, target_uri: str) -> dict[str, Any]:
        """返回节点/边数据，前端渲染血缘图。"""
```

### 4.3 TypeScript 类型

```typescript
// src/contracts/dataset.ts

export type DatasetStatus = 'draft' | 'published' | 'deprecated' | 'archived';

export interface DatasetSchema {
  fields: Record<string, { type: string; required?: boolean }>;
  primary_key: string[];
  metadata: Record<string, unknown>;
}

export interface DatasetVersion {
  dataset_id: string;
  version: string;
  status: DatasetStatus;
  schema: DatasetSchema;
  content_hash: string;
  row_count: number;
  size_bytes: number;
  created_at: string;
  created_by: string;
  lineage?: string;
  storage_uri: string;
}

export interface LineageRecord {
  record_id: string;
  target: string;
  source_type: 'task' | 'workflow' | 'manual' | 'external';
  source_ref: string;
  inputs: string[];
  outputs: string[];
  operation: string;
  timestamp: string;
  metadata: Record<string, unknown>;
}
```

### 4.4 现有 data_lake adapter 方案

**保留** `data_lake.py` 作为底层存储后端之一。新增 `app/training/contract_adapter.py`：

- `TrainingDataLake` → 实现 `IDatasetStore` 的 `LocalStorageBackend`
- 现有 `record_id` 去重 → 升级为 `content_hash` 内容寻址
- 新增 `datasets` / `dataset_versions` / `lineage_records` 三张表
- 训练代码（`trainer.py`）改造：从 `IDatasetStore.read()` 取数据，记录 `content_hash` 到 MLflow

---

## 5. 插件契约（Plugin & ExtensionPoint Contract）

### 5.1 设计目标

- 后端插件系统已成熟，按契约收口
- **新增**：前端扩展点机制（工作区可被插件注入 UI）
- **新增**：插件声明依赖的契约能力（`required_contracts`）
- 插件生命周期：install → enable → load → register → unload → disable → uninstall

### 5.2 Python 抽象基类

```python
# app/contracts/plugin.py
"""插件契约：定义插件、扩展点、生命周期的统一接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class PluginManifest:
    """插件清单契约（plugin.yaml）。"""
    id: str
    name: str
    version: str
    description: str
    author: str
    license: str
    entrypoint: str  # Python 模块路径，如 "plugins.ltc_chatter.main:Plugin"
    required_contracts: list[str] = field(default_factory=list)  # ["task@>=1.0", "dataset@>=1.0"]
    required_capabilities: list[str] = field(default_factory=list)  # ["dataset:write", "compute:gpu"]
    optional_capabilities: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)  # 其他插件 id
    config_schema: dict[str, Any] = field(default_factory=dict)  # Pydantic schema
    homepage: str = ""
    tags: list[str] = field(default_factory=list)


class IPlugin(ABC):
    """插件主类契约。"""

    @abstractmethod
    def manifest(self) -> PluginManifest: ...

    @abstractmethod
    async def on_load(self, context: "PluginContext") -> None:
        """插件加载时调用，注册任务处理器/扩展点。"""

    @abstractmethod
    async def on_unload(self) -> None: ...

    @abstractmethod
    def health_check(self) -> dict[str, Any]: ...


@dataclass
class PluginContext:
    """插件运行时上下文。"""
    plugin_id: str
    config: dict[str, Any]
    task_registry: Any  # ITaskRegistry
    dataset_store: Any  # IDatasetStore
    observability: Any  # IObservabilitySink
    logger: Any
    data_dir: str  # 插件私有数据目录


class IExtensionPoint(ABC):
    """扩展点契约。插件通过扩展点向核心注入能力。"""

    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """扩展点接受的输入 schema。"""

    @abstractmethod
    async def invoke(self, payload: dict[str, Any]) -> Any: ...


class IExtensionRegistry(ABC):
    """扩展点注册表契约。"""

    @abstractmethod
    def register(
        self,
        extension_point: str,
        plugin_id: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> None: ...

    @abstractmethod
    def list(self, extension_point: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def invoke(self, extension_point: str, payload: dict[str, Any]) -> list[Any]:
        """调用所有注册的扩展点，返回结果列表。"""


# 内置扩展点
class BUILTIN_EXTENSION_POINTS:
    TASK_HANDLER = "core.task_handler"  # 注册任务类型
    DATASET_READER = "core.dataset_reader"  # 自定义数据格式读取
    MODEL_REGISTRY = "core.model_registry"  # 注册可推理模型
    WORKFLOW_TEMPLATE = "core.workflow_template"  # 贡献工作流模板
    UI_WORKSPACE_PANEL = "core.ui.workspace_panel"  # 前端工作区面板
    UI_SETTINGS_TAB = "core.ui.settings_tab"  # 前端设置页 tab
    CHAT_COMMAND = "core.chat_command"  # 自然语言命令扩展
```

### 5.3 TypeScript 类型（前端扩展点）

```typescript
// src/contracts/plugin.ts

export interface PluginManifest {
  id: string;
  name: string;
  version: string;
  description: string;
  author: string;
  license: string;
  required_contracts: string[];
  required_capabilities: string[];
  optional_capabilities: string[];
  dependencies: string[];
  config_schema: Record<string, unknown>;
  homepage: string;
  tags: string[];
}

export interface ExtensionPointContribution {
  extension_point: string;
  plugin_id: string;
  // 前端扩展点用 component_url 加载远程组件（当前阶段：仅本地插件）
  component_url?: string;
  props?: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

// 前端扩展点注册表
export interface FrontendExtensionRegistry {
  register(contribution: ExtensionPointContribution): void;
  list(extension_point: string): ExtensionPointContribution[];
  // Vue 异步组件加载
  resolveComponent(contribution: ExtensionPointContribution): Promise<any>;
}
```

### 5.4 现有 plugin_system adapter 方案

- 现有 `PluginManager` 已经有 marketplace / 依赖解析 / 能力授权 / worker 隔离 → 升级为契约实现
- 现有 `PluginInfo` → `PluginManifest` 字段映射
- 新增 `app/plugins/extension_registry.py`，实现 `IExtensionRegistry`
- **前端新增** `src/composables/useExtensionRegistry.ts`，通过 SSE/WebSocket 接收后端扩展点贡献

---

## 6. 配置契约（ConfigSpec Contract）

### 6.1 设计目标

- 现有 `config.py` 是 dataclass + env，适合运行时配置，**不适合实验配置**
- **新增**：声明式 YAML 实验配置（继承 / 覆盖 / sweep）
- 实验配置与代码解耦，可版本化、可分享、可 diff
- 与 MLflow 集成：每次实验自动记录完整配置

### 6.2 Python 抽象基类

```python
# app/contracts/config.py
"""配置契约：定义声明式实验配置规格。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from pathlib import Path


@dataclass
class ConfigField:
    """配置字段规格。"""
    name: str
    type: str  # "int" / "float" / "str" / "bool" / "list" / "dict"
    default: Any
    description: str = ""
    required: bool = False
    choices: list[Any] = field(default_factory=list)
    min: Optional[float] = None
    max: Optional[float] = None
    sweep: Optional[dict[str, Any]] = None  # {"kind": "grid" / "random" / "bayesian", "values": [...]}


@dataclass
class ConfigSpec:
    """配置规格契约（对应一个 YAML 文件）。"""
    name: str
    version: str
    description: str
    fields: list[ConfigField]
    parent: Optional[str] = None  # 父配置 spec 名（继承）
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, values: dict[str, Any]) -> list[str]:
        """校验值是否符合规格，返回错误列表。"""

    def materialize(self, values: dict[str, Any]) -> dict[str, Any]:
        """填充默认值，返回完整配置字典。"""


class IConfigStore(ABC):
    """配置存储契约。"""

    @abstractmethod
    def register(self, spec: ConfigSpec) -> None: ...

    @abstractmethod
    def get_spec(self, name: str) -> ConfigSpec: ...

    @abstractmethod
    def load_yaml(self, path: str | Path) -> dict[str, Any]:
        """加载 YAML 配置文件，应用继承/覆盖。"""

    @abstractmethod
    def resolve(
        self,
        spec_name: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """合并 spec 默认值 + YAML + overrides，返回最终配置。"""

    @abstractmethod
    def expand_sweep(
        self,
        spec_name: str,
        sweep_config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """展开超参搜索，返回配置列表。"""


class IConfigSource(ABC):
    """配置源契约（多源合并：env / yaml / db / user_input）。"""

    @abstractmethod
    def priority(self) -> int: ...

    @abstractmethod
    def get(self, key: str) -> Any: ...

    @abstractmethod
    def keys(self) -> list[str]: ...
```

### 6.3 实验配置 YAML 示例

```yaml
# experiments/ltc_chatter_v3.yaml
spec: ltc_chatter
version: "3.0"
description: "LTC 颤振预测实验配置 v3"
parent: ltc_chatter_v2  # 继承 v2，覆盖部分字段

overrides:
  model:
    hidden_size: 64
    num_layers: 2
    dropout: 0.1
  training:
    epochs: 50
    batch_size: 32
    learning_rate:
      sweep:
        kind: grid
        values: [0.001, 0.0005, 0.0001]
  data:
    dataset_id: phm2010-milling
    version: "1.2.0"

metadata:
  paper_section: "Section 4.2"
  reproducibility_seed: 42
```

### 6.4 现有 config.py adapter 方案

- **保留** `config.py` 作为运行时配置（服务器/数据库/AI 网关等）
- **新增** `app/config/spec.py`，实现 `IConfigStore`，专管实验配置
- **新增** `app/config/yaml_loader.py`，YAML 解析 + 继承合并
- 现有 `LNN_<SECTION>_<KEY>` 环境变量 → 通过 `EnvConfigSource` 适配 `IConfigSource`

---

## 7. 可观测契约（Trace & Metric & Log & Snapshot Contract）

### 7.1 设计目标

- 统一埋点格式：任何模块都用同一套 trace/metric/log 接口
- **新增**：实验快照（git SHA + 数据 hash + 配置 + 模型 + 指标 → 一个不可变 snapshot）
- **新增**：一键复现入口（从 snapshot 恢复完整实验环境）
- 与 MLflow 集成，但 MLflow 不是唯一后端

### 7.2 Python 抽象基类

```python
# app/contracts/observability.py
"""可观测契约：定义 trace/metric/log/snapshot 的统一接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class TraceSpan:
    """trace span 契约。"""
    span_id: str
    trace_id: str
    parent_span_id: Optional[str] = None
    name: str = ""
    start_ts: float = 0.0
    end_ts: Optional[float] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # ok / error


@dataclass
class Metric:
    """metric 契约。"""
    name: str
    value: float
    timestamp: float
    labels: dict[str, str] = field(default_factory=dict)
    unit: str = ""


@dataclass
class LogEntry:
    """结构化日志契约。"""
    timestamp: float
    level: LogLevel
    message: str
    logger: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


@dataclass
class ExperimentSnapshot:
    """实验快照契约（一键复现的最小单元）。"""
    snapshot_id: str
    created_at: datetime
    created_by: str
    git_sha: str
    code_dirty: bool  # 是否有未提交修改
    config: dict[str, Any]
    dataset_versions: list[str]  # "dataset://xxx/v1"
    model_uri: str  # "model://ltc-v1"
    metrics: dict[str, float]
    environment: dict[str, str]  # python/packages 版本
    lineage_record_id: Optional[str] = None
    mlflow_run_id: Optional[str] = None
    notes: str = ""


class ITraceSink(ABC):
    @abstractmethod
    def start_span(self, name: str, parent: Optional[str] = None) -> str: ...

    @abstractmethod
    def end_span(self, span_id: str, status: str = "ok") -> None: ...

    @abstractmethod
    def add_attribute(self, span_id: str, key: str, value: Any) -> None: ...

    @abstractmethod
    def add_event(self, span_id: str, name: str, payload: dict[str, Any]) -> None: ...


class IMetricSink(ABC):
    @abstractmethod
    def counter(self, name: str, value: float = 1, labels: Optional[dict[str, str]] = None) -> None: ...

    @abstractmethod
    def gauge(self, name: str, value: float, labels: Optional[dict[str, str]] = None) -> None: ...

    @abstractmethod
    def histogram(self, name: str, value: float, labels: Optional[dict[str, str]] = None) -> None: ...


class ILogSink(ABC):
    @abstractmethod
    def log(self, entry: LogEntry) -> None: ...


class ISnapshotStore(ABC):
    """实验快照存储契约。"""

    @abstractmethod
    async def create(
        self,
        *,
        config: dict[str, Any],
        dataset_versions: list[str],
        model_uri: str,
        metrics: dict[str, float],
        created_by: str,
        notes: str = "",
    ) -> ExperimentSnapshot:
        """自动采集 git_sha / environment，写入存储。"""

    @abstractmethod
    async def get(self, snapshot_id: str) -> ExperimentSnapshot: ...

    @abstractmethod
    async def list(
        self, *, filters: Optional[dict[str, Any]] = None
    ) -> list[ExperimentSnapshot]: ...

    @abstractmethod
    async def reproduce(self, snapshot_id: str) -> str:
        """根据 snapshot 恢复环境并启动复现任务，返回 workflow_run_id。"""


class IObservabilitySink(ITraceSink, IMetricSink, ILogSink, ISnapshotStore):
    """可观测统一入口。业务模块通过此接口埋点，无需关心后端。"""
    pass
```

### 7.3 现有可观测设施 adapter 方案

| 现有设施 | adapter 实现 |
|---------|-------------|
| `app/utils/utils.py:MetricsCollector` | 实现 `IMetricSink`，Prometheus exposition 保留 |
| `app/core/logging_config.py` | 实现 `ILogSink`，JSONFormatter + 脱敏保留 |
| `app/ai/lnn/tracking/mlflow_tracker.py` | MLflow 作为 snapshot 后端之一 |
| `app/ai/lnn/training/reproducibility.py` | 在 snapshot 创建时强制调用 |
| **新增** `app/observability/trace.py` | 实现 `ITraceSink`，初期用内存 + 文件 |
| **新增** `app/observability/snapshot.py` | 实现 `ISnapshotStore`，新建 `experiment_snapshots` 表 |
| **新增** `app/observability/git_collector.py` | 采集 git SHA + dirty 状态 |

**关键改造**：
- 训练代码（`trainer.py` 第 454-619 行）改造：训练结束自动调用 `ISnapshotStore.create()`
- 新增 `/api/v1/snapshots` 路由，前端"一键复现"按钮调用 `reproduce()`

---

## 8. 与现有 LTC / RAG / SHARP 模块的接入方案

### 8.1 总体策略

业务模块**不重写**，通过**adapter 适配契约** + **声明插件 manifest** 两步接入。

### 8.2 LTC 颤振预测模块接入

**现状**：`app/ai/lnn/` 已有完整训练/推理/量化/路由代码，但与核心耦合。

**接入步骤**：

1. 新建 `plugins/ltc_chatter/plugin.yaml`：
```yaml
id: ltc_chatter
name: LTC 颤振预测
version: 2.5.0
author: 灵境制造
license: MIT
entrypoint: plugins.ltc_chatter.main:Plugin
required_contracts:
  - "task@>=1.0"
  - "dataset@>=1.0"
  - "config@>=1.0"
  - "observability@>=1.0"
required_capabilities:
  - "dataset:write"
  - "compute:gpu"
  - "observability:snapshot:create"
config_schema:
  type: object
  properties:
    model_type: {type: string, default: ltc, choices: [ltc, cfc, hybrid]}
    hidden_size: {type: integer, default: 32, min: 8, max: 256}
tags: [chatter, ltc, lnn, manufacturing]
```

2. 新建 `plugins/ltc_chatter/main.py`：
```python
class Plugin(IPlugin):
    def manifest(self) -> PluginManifest: ...

    async def on_load(self, ctx: PluginContext) -> None:
        # 注册任务处理器
        ctx.task_registry.register(LTCTrainHandler(), plugin_id="ltc_chatter")
        ctx.task_registry.register(LTCInferHandler(), plugin_id="ltc_chatter")
        # 注册模型
        await ctx.extension_registry.invoke(
            "core.model_registry",
            {"model_id": "ltc-default", "loader": "plugins.ltc_chatter.model:load"},
        )
        # 注册工作流模板
        await ctx.extension_registry.invoke(
            "core.workflow_template",
            {"name": "LTC 训练+评估", "spec": "workflows/ltc_train_eval.yaml"},
        )

    async def on_unload(self) -> None: ...
```

3. 现有 `app/ai/lnn/trainer.py` 改造点：
   - 数据加载从 `IDatasetStore.read()` 取（替代直接读 JSONL）
   - 训练配置从 `IConfigStore.resolve("ltc_chatter", overrides)` 取（替代硬编码）
   - 训练结束调用 `ISnapshotStore.create()`（替代手动 MLflow log）
   - 推理路径实现 `TaskHandler` 协议，通过 `ITaskExecutor.submit("ltc_infer", ...)` 调用

4. **不删除** `app/ai/lnn/`，作为 `plugins/ltc_chatter/` 的实现细节（符号链接或 re-export）

### 8.3 RAG 检索模块接入

**现状**：RAG 引擎散落在 `app/rag/` 多个文件，HybridInferenceEngine 未生产使用。

**接入步骤**：

1. 新建 `plugins/rag_retrieval/plugin.yaml`：
```yaml
id: rag_retrieval
name: RAG 检索
version: 2.5.0
entrypoint: plugins.rag_retrieval.main:Plugin
required_contracts: ["task@>=1.0", "dataset@>=1.0", "observability@>=1.0"]
required_capabilities: ["dataset:write"]
tags: [rag, retrieval, knowledge]
```

2. 任务处理器注册：
   - `RAGIndexHandler`：索引文档，输出 `dataset://rag-corpus/vN`
   - `RAGQueryHandler`：查询，输出 `metrics` artifact

3. 现有 `RagRetrievalEngine` → 包装为 `RAGQueryHandler.execute()`

4. HybridInferenceEngine（TaskRouter + DempsterShaferFusion）→ 显式标记为 experimental，作为 `RAGQueryHandler` 的可选 fusion 策略

### 8.4 SHARP 三元组验证智能体接入

**现状**：`app/agents/sharp/` 已实现 5 维评估。

**接入步骤**：

1. 新建 `plugins/sharp_agent/plugin.yaml`
2. `SharpAgent` → 实现 `TaskHandler`，任务类型 `sharp_review`
3. 工作流模板 `workflows/sharp_review.yaml`：输入论文 → SHARP 评估 → 生成报告

### 8.5 其他模块接入

| 模块 | 插件 id | 主要任务类型 | 接入优先级 |
|------|--------|-------------|-----------|
| 数控加工仿真 | `machining_sim` | `sim_run` / `sim_chatter_analyze` | 阶段 1 |
| CAM / 工艺规划 | `process_planning` | `cam_generate` / `process_plan` | 阶段 2 |
| 工艺理解 NL2CAD | `process_understanding` | `nl2cad` | 阶段 2 |
| 飞轮反馈闭环 | `data_flywheel` | `flywheel_collect` / `flywheel_retrain` | 阶段 4 |
| 世界模型 | `world_model` | `wm_predict_state` | 阶段 8 |
| RL agent | `rl_agent` | `rl_act` | 阶段 8 |

---

## 9. OpenAPI 自动生成与前后端契约同步

### 9.1 生成机制

- 后端契约抽象基类用 Pydantic v2 模型描述输入输出
- FastAPI 路由签名引用契约模型，FastAPI 自动生成 OpenAPI schema
- 新增 `app/contracts/openapi_gen.py`：脚本导出 `openapi.json` 到 `docs/api/openapi.json`（已存在，需更新）
- 前端 `src/contracts/` 类型通过 `openapi-typescript` 从 `openapi.json` 生成
- CI 强制校验：`openapi.json` 与代码一致，`src/contracts/` 与 `openapi.json` 一致

### 9.2 版本化

- API 路径保留 `/api/v1/` 前缀
- 契约 breaking change → `/api/v2/`，旧版本至少保留 2 个版本
- OpenAPI `info.version` 与 git tag 同步

---

## 10. 分阶段实现路线图（详细改造点）

### 阶段 0: 契约定型（1-2 周，不写功能代码）

**目标**：契约代码骨架 + 单元测试，不接入业务。

**任务清单**：
- [ ] 创建 `app/contracts/` 目录，落盘 5 个抽象基类文件
- [ ] 创建 `src/contracts/` 目录，落盘 5 个 TypeScript 类型文件
- [ ] 新增 `app/contracts/__init__.py` 导出所有契约
- [ ] 新增 `app/contracts/openapi_gen.py` OpenAPI 生成脚本
- [ ] 编写契约单元测试 `app/contracts/tests/test_*.py`（测契约本身：状态机转换合法性、DAG 校验、schema 校验、配置继承合并、snapshot 字段完整性）
- [ ] 更新 CI：增加契约测试 job + openapi 一致性检查 job
- [ ] **不动**任何现有业务代码

**验收标准**：
- 所有契约测试通过
- `openapi.json` 生成成功且包含 5 大契约 schema
- 前端 `src/contracts/` 类型可被现有代码 import 不报错

**涉及文件**（新增，约 15 个）：
- `app/contracts/{task,dataset,plugin,config,observability}.py`
- `app/contracts/__init__.py`
- `app/contracts/openapi_gen.py`
- `app/contracts/tests/test_{task,dataset,plugin,config,observability}.py`
- `src/contracts/{task,dataset,plugin,config,observability}.ts`
- `src/contracts/index.ts`

---

### 阶段 1: 任务编排系统（2-3 周）

**目标**：在 AsyncTaskManager 之上增加 Workflow DAG 编排层。

**任务清单**：
- [ ] 新增 `app/workflow/` 目录
- [ ] 实现 `WorkflowRunner`（基于 networkx DAG，支持并行/串行/断点续跑）
- [ ] 实现 `app/tasks/contract_adapter.py`：AsyncTaskManager → `ITaskExecutor`
- [ ] 实现 `app/tasks/registry.py`：`ITaskRegistry`，插件入口点注册
- [ ] 新增数据库表 `workflow_runs` / `workflow_run_nodes`
- [ ] 新增 `/api/v1/workflows` 路由（CRUD + run + cancel + SSE）
- [ ] 前端 `TaskBoard.vue` 扩展为 Workflow 面板（DAG 可视化 + 节点状态）
- [ ] 前端 `src/composables/useWorkflow.ts`
- [ ] 工作流模板 YAML 格式定义 + 内置模板（"数据预处理→训练→评估→报告"）

**验收标准**：
- 可定义 5 节点 DAG，一键跑通，节点失败时下游自动 SKIPPED
- 断点续跑：失败节点修复后从失败点继续，不重跑已完成节点
- 前端 DAG 可视化显示节点状态实时更新

**涉及文件**：
- 新增：`app/workflow/{runner,registry,validator,dag_store}.py`、`app/api/v1/workflows.py`
- 修改：`app/tasks/task_system.py`（仅增加 adapter 引用，不动核心）、`src/views/TaskBoard.vue`（扩展）、`src/router/index.ts`（新路由）

---

### 阶段 2: 可复现性基础设施（2-3 周）

**目标**：重写 data_lake 为 Dataset 抽象 + 实验快照 + 一键复现。

**任务清单**：
- [ ] 实现 `app/data/dataset_store.py`：`IDatasetStore`（基于 SQLite 元数据 + 文件系统内容）
- [ ] 实现 `app/data/lineage_store.py`：`ILineageStore`
- [ ] 实现 `app/training/contract_adapter.py`：`TrainingDataLake` → `IDatasetStore` 后端之一
- [ ] 新增数据库表 `datasets` / `dataset_versions` / `lineage_records`
- [ ] 实现 `app/observability/snapshot.py`：`ISnapshotStore`
- [ ] 实现 `app/observability/git_collector.py`：git SHA + dirty 检测
- [ ] 实现 `app/observability/trace.py`：`ITraceSink`（内存 + JSONL 文件）
- [ ] 改造 `app/ai/lnn/training/trainer.py`：
  - 数据从 `IDatasetStore.read()` 取
  - 训练结束调用 `ISnapshotStore.create()`，强制记录 git SHA + 数据 hash
  - 替代当前手动 MLflow log（保留 MLflow 作为后端之一）
- [ ] 新增 `/api/v1/snapshots` 路由 + `/api/v1/datasets` 路由
- [ ] 前端"实验快照"视图 + "一键复现"按钮

**验收标准**：
- 同一 snapshot 在干净环境复现，关键指标差异 < 1%
- git SHA + 数据 hash + 完整配置写入 snapshot，可查询
- 血缘图可视化：从模型反查训练数据/任务/配置

**涉及文件**：
- 新增：`app/data/{dataset_store,lineage_store}.py`、`app/observability/{snapshot,trace,git_collector}.py`、`app/api/v1/{datasets,snapshots}.py`
- 修改：`app/ai/lnn/training/trainer.py`（第 454-619 行）、`app/training/data_lake.py`（增加 adapter，保留旧接口）、`app/database/models.py`（新表）

---

### 阶段 3: 插件系统骨架 + 配置系统（3-4 周）

**目标**：后端插件按契约收口 + 前端插件化机制 + 声明式 YAML 配置。

**任务清单**：
- [ ] 实现 `app/plugins/contract_adapter.py`：现有 `PluginManager` → 契约实现
- [ ] 实现 `app/plugins/extension_registry.py`：`IExtensionRegistry`
- [ ] 插件 manifest 格式定型（`plugin.yaml` schema）
- [ ] 插件入口点机制（Python `entry_points` 或显式注册）
- [ ] 前端 `src/composables/useExtensionRegistry.ts`
- [ ] 前端 `src/components/WorkspacePanelHost.vue`：工作区扩展点宿主
- [ ] 实现 `app/config/spec.py`：`IConfigStore`
- [ ] 实现 `app/config/yaml_loader.py`：YAML 解析 + 继承合并
- [ ] 实现 `app/config_contract_adapter.py`：现有 `config.py` 环境变量 → `IConfigSource`
- [ ] 超参搜索展开（grid / random / bayesian）

**验收标准**：
- 一个示例插件可安装/启用/禁用/卸载，生命周期完整
- 前端工作区可加载插件贡献的面板组件
- 实验配置 YAML 支持继承 + 覆盖 + sweep 展开

**涉及文件**：
- 新增：`app/plugins/{contract_adapter,extension_registry}.py`、`app/config/{spec,yaml_loader}.py`、`app/config_contract_adapter.py`、`src/composables/useExtensionRegistry.ts`、`src/components/WorkspacePanelHost.vue`
- 修改：`app/plugins/plugin_system.py`（增加 adapter 引用）

---

### 阶段 4: 数据飞轮（2-3 周）

**目标**：飞轮指标接入真实数据源，反馈闭环落地。

**任务清单**：
- [ ] 实现 `plugins/data_flywheel/plugin.yaml`
- [ ] 反馈采集层：用户标注 / 采纳 / 修正记录写入 `IDatasetStore`
- [ ] 模型迭代管线：定义为 Workflow 模板（采集→训练→评估→模型市场→热更新）
- [ ] 改造 `app/metrics/flywheel_metrics.py`：从 `IDatasetStore` / `ISnapshotStore` 取真实数据（替换硬编码）
- [ ] 模型热更新机制：训练完成 → 注册新模型 → 灰度切换
- [ ] 前端飞轮看板接入真实数据

**验收标准**：
- `flywheel_metrics` 5 个指标全部来自真实数据源
- 用户采纳率从 0% 开始，随使用增长
- 完整飞轮闭环可手动触发跑通

**涉及文件**：
- 新增：`plugins/data_flywheel/` 整个目录
- 修改：`app/metrics/flywheel_metrics.py`、`app/api/v1/flywheel.py`（响应字段不变，数据源替换）

---

### 阶段 5: CLI + SDK + HTTP API 契约（2 周）

**目标**：软件可被脚本/notebook 调用。

**任务清单**：
- [ ] Python SDK `lomo`（`from lomo import Workflow, Dataset, Snapshot`）
- [ ] 用户 CLI（基于 typer）：`lomo train/predict/dataset/snapshot/workflow`
- [ ] HTTP API 版本化契约（`/api/v1/` 全部对齐契约）
- [ ] SDK 文档 + 示例 notebook

**验收标准**：
- 外部 Python 脚本可调用 SDK 跑通完整工作流
- CLI 命令与 HTTP API 行为一致

---

### 阶段 6: 工作流模板 + 协作层（2-3 周）

**目标**：生态内容飞轮。

**任务清单**：
- [ ] 工作流模板市场（基于插件市场扩展）
- [ ] 项目级 Git 同步（数据集 + 模型 + 配置 + 工作流）
- [ ] 模型/数据集卡片（README + 指标 + lineage）
- [ ] 项目导入导出（`.lomo` 包格式）

---

### 阶段 7: 可解释性可视化（2 周）

**目标**：power user 粘性。

**任务清单**：
- [ ] LTC 隐状态/注意力可视化（基于 `app/ai/lnn/inference/streaming.py` 的 PagedHiddenState）
- [ ] 反事实解释（修改输入特征，观察输出变化）
- [ ] 置信度披露（MC dropout 不确定性可视化）

---

### 阶段 8: 世界模型 / RL 模块（远期，作为插件接入）

**目标**：完整"感知-预测-决策"闭环。

**任务清单**：
- [ ] `plugins/world_model/`：世界模型作为"过程状态预测器"插件，实现 `TaskHandler`
- [ ] `plugins/rl_agent/`：RL agent 作为"决策策略"插件
- [ ] 嵌入工作流模板：感知（传感器）→ 预测（世界模型）→ 决策（RL）→ 执行（仿真）
- [ ] RL 训练管线（基于阶段 1 的 Workflow + 阶段 2 的 Snapshot）

---

## 11. 契约变更管理流程

### 11.1 变更类型

| 类型 | 含义 | 流程 |
|------|------|------|
| **Patch** | 字段补默认值、新增可选字段 | 直接 PR + 单元测试 |
| **Minor** | 新增方法、新增枚举值 | ADR 评审 + 兼容性测试 |
| **Major** | 删除字段、改语义、改签名 | 新 ADR + Deprecation 期 ≥ 1 版本 |

### 11.2 评审清单

契约 PR 必须包含：
- [ ] 契约抽象基类修改 + 单元测试更新
- [ ] TypeScript 类型同步
- [ ] OpenAPI schema 更新
- [ ] 现有 adapter 影响评估
- [ ] 文档更新（本文档 + 对应 ADR）
- [ ] 向后兼容性验证（或明确的 Deprecation 路径）

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 契约设计偏差，后续大改 | 高 | 阶段 0 只定型不实现，先用 LTC 模块验证接入可行性 |
| adapter 层性能开销 | 中 | adapter 仅做接口翻译，无额外 IO；性能敏感路径直接调底层 |
| 现有功能因重构回归 | 高 | 现有模块不删除，adapter 优先，分阶段切换 |
| 前端插件化机制复杂 | 中 | 阶段 3 先做后端插件契约，前端扩展点先支持本地插件，远程组件后期 |
| 世界模型/RL 远期接入点过时 | 中 | 阶段 8 走完整 ADR 流程，必要时新建 ADR-006 |

---

## 13. 与现有 ADR 的关系

| ADR | 主题 | 与本契约的关系 |
|-----|------|---------------|
| [ADR-001](../adr/ADR-001-LNN-AI引擎选型.md) | LNN AI 引擎选型 | LTC 模块作为插件接入，LNN 定义保持一致（液态神经网络） |
| [ADR-002](../adr/ADR-002-FastAPI后端框架选型.md) | FastAPI 后端选型 | 后端契约 Python 实现仍基于 FastAPI |
| [ADR-003](../adr/ADR-003-SQLite主数据库选型.md) | SQLite 主数据库 | 新增表（datasets/workflow_runs/snapshots）仍用 SQLite |
| [ADR-004](../adr/ADR-004-SHARP三元组验证智能体.md) | SHARP 智能体 | SHARP 作为插件接入，三元组验证逻辑不变 |
| [ADR-005](../adr/ADR-005-核心架构契约设计.md) | 本契约的决策 ADR | 本文档是 ADR-005 的详细展开 |

---

## 14. 待 review 决策点

请项目负责人重点 review 以下决策点，确认后进入阶段 0 实现：

1. **五大契约划分是否合理**：是否需要增减？（如：是否拆出"安全契约"独立？）
2. **核心 vs 插件边界**：飞轮归属插件（依赖核心数据/任务契约）是否同意？还是应作为核心？
3. **adapter 策略**：保留现有模块不重写、通过 adapter 适配，是否同意？还是部分模块直接重写？
4. **阶段顺序**：阶段 1（任务编排）→ 阶段 2（可复现性）的顺序是否同意？还是优先级互换？
5. **前端插件化范围**：阶段 3 是否需要支持远程插件（动态加载 Vue 组件），还是仅本地插件？
6. **MLflow 定位**：保留 MLflow 作为可观测后端之一，还是迁移到自研 snapshot 存储？
7. **世界模型/RL 时间点**：阶段 8 是否需要在阶段 0 就预留接入点（如 `wm_predict_state` 任务类型占位）？

确认后，阶段 0 立即开始落盘契约代码骨架。

---

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|----------|--------|
| 2026-07-13 | 初始版本，五大契约详细接口定义 + 边界划分 + 接入方案 + 路线图 | 项目负责人 |
