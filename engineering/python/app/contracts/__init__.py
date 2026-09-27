"""核心架构契约层（Core Contracts Layer）.

本包定义灵境制造从"功能堆叠"演进为"生态平台"所需的五大核心契约：
    - 任务契约（Task / Workflow DAG）
    - 数据契约（Dataset / Version / Lineage）
    - 插件契约（Plugin / ExtensionPoint）
    - 配置契约（ConfigSpec / YAML 继承）
    - 可观测契约（Trace / Metric / Log / Snapshot）

契约稳定性承诺：
    1. 契约接口一经 ADR 评审通过，标记为 Stable，只能向后兼容扩展
    2. 任何 breaking change 必须新开 ADR，标注 Deprecates，并提供至少一个版本的兼容期
    3. 契约代码与实现代码分离：契约在 app/contracts/，实现在各业务模块
    4. 契约变更必须同步更新 OpenAPI schema 与 TypeScript 类型，CI 强制校验

参考文档：
    - docs/adr/ADR-005-核心架构契约设计.md
    - docs/development/core-contracts-design.md
"""

from app.contracts.config import (
    ConfigField,
    ConfigSpec,
    IConfigSource,
    IConfigStore,
)
from app.contracts.dataset import (
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    IDatasetStore,
    ILineageStore,
    LineageRecord,
)
from app.contracts.observability import (
    ExperimentSnapshot,
    ILogSink,
    IMetricSink,
    IObservabilitySink,
    ISnapshotStore,
    ITraceSink,
    LogEntry,
    LogLevel,
    Metric,
    TraceSpan,
)
from app.contracts.plugin import (
    BUILTIN_EXTENSION_POINTS,
    ExtensionPointContribution,
    IExtensionPoint,
    IExtensionRegistry,
    IPlugin,
    PluginContext,
    PluginManifest,
)
from app.contracts.project_package import (
    ConflictStrategy,
    ContentPolicy,
    DEFAULT_MAX_FILE_SIZE_BYTES,
    ExportOptions,
    ExportResult,
    IProjectPackageService,
    ImportOptions,
    ImportResourceRecord,
    ImportResult,
    PACKAGE_FILE_EXTENSION,
    PACKAGE_FILENAME_TEMPLATE,
    PackageFormatVersion,
    PackageManifest,
    PackageProjectInfo,
    PackageResourceEntry,
    PackageTaskStatus,
    SOURCE_MACHINE_INFO_DEFAULTS,
    STREAM_BUFFER_SIZE,
    SourceMachineInfo,
    ValidationResult,
)
from app.contracts.project_sync import (
    DEFAULT_SYNC_STRATEGY,
    RESOURCE_TYPES,
    SYNC_DIRECTIONS,
    SYNC_STATUS,
    SYNC_STRATEGIES,
    ProjectSyncManifest,
    ResourceRef,
    SyncRecord,
    build_resource_uri,
    parse_resource_uri,
)
from app.contracts.resource_card import (
    DatasetCard,
    DatasetReadme,
    DatasetReadmeScope,
    IResourceCardService,
    LineageSummary,
    ModelArtifact,
    ModelArtifactStatus,
    ModelArtifactType,
    VALID_MODEL_STATUS_TRANSITIONS,
)
from app.contracts.task import (
    Artifact,
    ITaskExecutor,
    ITaskRegistry,
    IWorkflowRunner,
    TaskContext,
    TaskHandler,
    TaskPriority,
    TaskProgress,
    TaskResult,
    TaskStatus,
    WorkflowEdge,
    WorkflowEvent,
    WorkflowNode,
    WorkflowSpec,
)
from app.contracts.workflow_template import (
    TEMPLATE_CATEGORIES,
    TemplateMarketStats,
    WorkflowTemplateManifest,
)

# 世界模型契约（ADR-017 阶段 8 p8）
from app.contracts.world_model import (
    DEFAULT_ACTION_DIM,
    DEFAULT_HORIZON,
    DEFAULT_STATE_DIM,
    MAX_HORIZON,
    MIN_HORIZON,
    ActionField,
    InvalidStateError,
    ModelNotFoundError,
    PredictionError,
    StateField,
    TrajectoryMetrics,
    TrajectoryStep,
    WorldModelError,
    WorldModelInfo,
    WorldModelPredictRequest,
    WorldModelPredictResponse,
    WorldModelVersion,
    WM_PREDICT_STATE_TASK_TYPE,
)

# RL Agent 契约（ADR-017 阶段 8 p8）
from app.contracts.rl_agent import (
    ActionEvaluation,
    OptimizationTarget,
    PolicyAlgorithm,
    PolicyInfo,
    PolicyNotFoundError,
    PolicyVersion,
    PolicyError,
    RLAgentError,
    RLActRequest,
    RLActResponse,
    RecommendedAction,
    RL_ACT_TASK_TYPE,
    SafetyConstraintsSpec,
    SafetyViolationError,
    TrainingAlreadyRunningError,
    TrainingError,
    TrainingMetricsSnapshot,
    TrainingStartRequest,
    TrainingStatus,
    TrainingStatusInfo,
)

# 可解释性契约（ADR-016 阶段 7 p7）
from app.contracts.explainability import (
    ComparisonMismatchError,
    ComparisonType,
    ConfidenceExplanation,
    CounterfactualExplanation,
    ExplanationComparison,
    ExplanationLookupError,
    ExplanationRecord,
    ExplanationRequest,
    ExplanationType,
    ExplanationValidationError,
    ExplainabilityError,
    GateDynamicsExplanation,
    HiddenStateExplanation,
    IExplainabilityService,
    ProjectionError,
    ProjectionMethod,
    SamplingError,
)

# 跨领域共享通用 schema（P2-5 重构）
from app.contracts._shared import (
    ErrorResponse,
    HealthResponse,
    MessageResponse,
    PaginatedResponse,
    TaskListResponse,
    TimestampedModel,
)

__all__ = [
    # 任务契约
    "Artifact",
    "ITaskExecutor",
    "ITaskRegistry",
    "IWorkflowRunner",
    "TaskContext",
    "TaskHandler",
    "TaskPriority",
    "TaskProgress",
    "TaskResult",
    "TaskStatus",
    "WorkflowEdge",
    "WorkflowEvent",
    "WorkflowNode",
    "WorkflowSpec",
    # 工作流模板契约（ADR-010）
    "TEMPLATE_CATEGORIES",
    "TemplateMarketStats",
    "WorkflowTemplateManifest",
    # 数据契约
    "DatasetSchema",
    "DatasetStatus",
    "DatasetVersion",
    "IDatasetStore",
    "ILineageStore",
    "LineageRecord",
    # 插件契约
    "BUILTIN_EXTENSION_POINTS",
    "ExtensionPointContribution",
    "IExtensionPoint",
    "IExtensionRegistry",
    "IPlugin",
    "PluginContext",
    "PluginManifest",
    # 项目级 Git 同步契约（ADR-011）
    "DEFAULT_SYNC_STRATEGY",
    "RESOURCE_TYPES",
    "SYNC_DIRECTIONS",
    "SYNC_STATUS",
    "SYNC_STRATEGIES",
    "ProjectSyncManifest",
    "ResourceRef",
    "SyncRecord",
    "build_resource_uri",
    "parse_resource_uri",
    # 资源卡片契约（ADR-012）
    "DatasetCard",
    "DatasetReadme",
    "DatasetReadmeScope",
    "IResourceCardService",
    "LineageSummary",
    "ModelArtifact",
    "ModelArtifactStatus",
    "ModelArtifactType",
    "VALID_MODEL_STATUS_TRANSITIONS",
    # 项目导入导出契约（ADR-015）
    "ConflictStrategy",
    "ContentPolicy",
    "DEFAULT_MAX_FILE_SIZE_BYTES",
    "ExportOptions",
    "ExportResult",
    "IProjectPackageService",
    "ImportOptions",
    "ImportResourceRecord",
    "ImportResult",
    "PACKAGE_FILE_EXTENSION",
    "PACKAGE_FILENAME_TEMPLATE",
    "PackageFormatVersion",
    "PackageManifest",
    "PackageProjectInfo",
    "PackageResourceEntry",
    "PackageTaskStatus",
    "SOURCE_MACHINE_INFO_DEFAULTS",
    "STREAM_BUFFER_SIZE",
    "SourceMachineInfo",
    "ValidationResult",
    # 配置契约
    "ConfigField",
    "ConfigSpec",
    "IConfigSource",
    "IConfigStore",
    # 可观测契约
    "ExperimentSnapshot",
    "ILogSink",
    "IMetricSink",
    "IObservabilitySink",
    "ISnapshotStore",
    "ITraceSink",
    "LogEntry",
    "LogLevel",
    "Metric",
    "TraceSpan",
    # 世界模型契约（ADR-017 阶段 8 p8）
    "DEFAULT_ACTION_DIM",
    "DEFAULT_HORIZON",
    "DEFAULT_STATE_DIM",
    "MAX_HORIZON",
    "MIN_HORIZON",
    "ActionField",
    "InvalidStateError",
    "ModelNotFoundError",
    "PredictionError",
    "StateField",
    "TrajectoryMetrics",
    "TrajectoryStep",
    "WorldModelError",
    "WorldModelInfo",
    "WorldModelPredictRequest",
    "WorldModelPredictResponse",
    "WorldModelVersion",
    "WM_PREDICT_STATE_TASK_TYPE",
    # RL Agent 契约（ADR-017 阶段 8 p8）
    "ActionEvaluation",
    "OptimizationTarget",
    "PolicyAlgorithm",
    "PolicyInfo",
    "PolicyNotFoundError",
    "PolicyVersion",
    "PolicyError",
    "RLAgentError",
    "RLActRequest",
    "RLActResponse",
    "RecommendedAction",
    "RL_ACT_TASK_TYPE",
    "SafetyConstraintsSpec",
    "SafetyViolationError",
    "TrainingAlreadyRunningError",
    "TrainingError",
    "TrainingMetricsSnapshot",
    "TrainingStartRequest",
    "TrainingStatus",
    "TrainingStatusInfo",
    # 可解释性契约（ADR-016 阶段 7 p7）
    "ComparisonMismatchError",
    "ComparisonType",
    "ConfidenceExplanation",
    "CounterfactualExplanation",
    "ExplanationComparison",
    "ExplanationLookupError",
    "ExplanationRecord",
    "ExplanationRequest",
    "ExplanationType",
    "ExplanationValidationError",
    "ExplainabilityError",
    "GateDynamicsExplanation",
    "HiddenStateExplanation",
    "IExplainabilityService",
    "ProjectionError",
    "ProjectionMethod",
    "SamplingError",
    # 跨领域共享通用 schema（P2-5 重构）
    "ErrorResponse",
    "HealthResponse",
    "MessageResponse",
    "PaginatedResponse",
    "TaskListResponse",
    "TimestampedModel",
    "CuttingParameters",
    "CuttingResults",
    "MachiningAnomaly",
    "CuttingExperience",
    "ExperienceQuery",
    "ExperienceStats",
]

__version__ = "1.0.0"
CONTRACTS_VERSION = "1.0.0"  # 契约版本号，用于 OpenAPI info.version 与 adapter 兼容性校验

from app.contracts.cutting_experience import (
    CuttingParameters,
    CuttingResults,
    MachiningAnomaly,
    CuttingExperience,
    ExperienceQuery,
    ExperienceStats,
)
