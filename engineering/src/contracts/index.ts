/**
 * 灵境制造 — 核心架构契约统一入口
 *
 * 对应后端 python/app/contracts/__init__.py。
 * 详见 docs/development/core-contracts-design.md 与 docs/adr/ADR-005-核心架构契约设计.md。
 *
 * 五大核心契约：
 *   1. Task / Workflow  — 任务与 DAG 工作流
 *   2. Dataset / Lineage — 数据集版本与血缘
 *   3. Plugin / ExtensionPoint — 插件与扩展点
 *   4. ConfigSpec — 声明式实验配置
 *   5. Observability — Trace / Metric / Log / Snapshot
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

// Task & Workflow 契约
export type {
  TaskStatus,
  TaskPriority,
  ArtifactType,
  Artifact,
  TaskContext,
  TaskResult,
  TaskProgress,
  TaskHandlerDescriptor,
  WorkflowNode,
  WorkflowEdge,
  WorkflowSpec,
  WorkflowEventType,
  WorkflowEvent,
  WorkflowRunStatus,
} from './task';

export type { ITaskExecutor, IWorkflowRunner } from './task';

// Dataset & Lineage 契约
export type {
  DatasetStatus,
  DatasetFieldType,
  DatasetFieldDef,
  DatasetSchema,
  DatasetVersion,
  LineageSourceType,
  LineageRecord,
  DatasetSummary,
} from './dataset';

export type { IDatasetStore, ILineageStore } from './dataset';

// Plugin & ExtensionPoint 契约
export type {
  PluginManifest,
  PluginStatus,
  PluginInfo,
  ExtensionPointContribution,
  BuiltinExtensionPoint,
  Capability,
  BuiltinCapabilityName,
  IExtensionRegistry,
} from './plugin';

export {
  BUILTIN_EXTENSION_POINTS,
  CONTRACTS_PLUGIN_VERSION,
} from './plugin';

// ConfigSpec 契约
export type {
  ConfigFieldType,
  SweepKind,
  SweepSpec,
  ConfigField,
  ConfigSpec,
  IConfigStore,
  IConfigSource,
} from './config';

export {
  VALID_FIELD_TYPES,
  VALID_SWEEP_KINDS,
  CONTRACTS_CONFIG_VERSION,
} from './config';

// Observability 契约
export type {
  LogLevel,
  SpanStatus,
  TraceSpan,
  Metric,
  LogEntry,
  ExperimentSnapshot,
  CreateSnapshotParams,
  SnapshotFilters,
  ITraceSink,
  IMetricSink,
  ILogSink,
  ISnapshotStore,
  IObservabilitySink,
} from './observability';

export {
  LOG_LEVELS,
  VALID_SPAN_STATUSES,
  CONTRACTS_OBSERVABILITY_VERSION,
} from './observability';

// Workflow Template Marketplace 契约（ADR-010 阶段 6 p6-1）
export type {
  TemplateCategory,
  TemplateStatus,
  TemplateSortBy,
  WorkflowTemplateManifest,
  WorkflowSpecPayload,
  TemplateMarketStats,
  WorkflowTemplateSummary,
  WorkflowTemplateVersionSummary,
  ListTemplatesParams,
  ListTemplatesResponse,
  SearchTemplatesResponse,
  GetTemplateResponse,
  DownloadTemplateResponse,
  ListVersionsResponse,
  MarketStatsResponse,
  PublishTemplateRequest,
  PublishTemplateResponse,
  RateTemplateRequest,
  RateTemplateResponse,
  UnpublishTemplateResponse,
  TemplateValidationErrorResponse,
} from './workflow_template';

export {
  TEMPLATE_ID_PATTERN,
  TEMPLATE_SEMVER_PATTERN,
  TEMPLATE_CATEGORIES,
  TEMPLATE_CATEGORY_VALUES,
  TEMPLATE_CATEGORY_LABELS,
} from './workflow_template';

// Project Git Sync 契约（ADR-011 阶段 6 p6-2）
export type {
  ResourceType,
  SyncStrategy,
  SyncStatus,
  SyncDirection,
  ResourceRefMetadata,
  ResourceRef,
  ResourceRefRecord,
  ProjectSyncManifest,
  GetProjectResponse,
  SyncRecord,
  ProjectStatusResponse,
  ChangedFileEntry,
  CreateProjectRequest,
  CloneProjectRequest,
  ListProjectsParams,
  ListProjectsResponse,
  CommitProjectRequest,
  CommitProjectResponse,
  SyncOperationResponse,
  DeleteProjectResponse,
  AddResourceRefRequest,
  AddResourceRefResponse,
  ListResourceRefsParams,
  ListResourceRefsResponse,
  RemoveResourceRefResponse,
  ListSyncRecordsParams,
  ListSyncRecordsResponse,
} from './project_sync';

export {
  RESOURCE_TYPES,
  RESOURCE_TYPE_VALUES,
  RESOURCE_TYPE_LABELS,
  SYNC_STRATEGIES,
  SYNC_STRATEGY_VALUES,
  SYNC_STRATEGY_LABELS,
  SYNC_STATUS,
  SYNC_STATUS_VALUES,
  SYNC_STATUS_LABELS,
  SYNC_STATUS_TAG_TYPE,
  SYNC_DIRECTIONS,
  SYNC_DIRECTION_LABELS,
  DEFAULT_SYNC_STRATEGY,
  parseResourceUri,
  buildResourceUri,
  isResourceType,
  isSyncStrategy,
  isSyncStatus,
} from './project_sync';

// Resource Cards 契约（ADR-012 阶段 6 p6-3：模型产物 + 数据集 README + 卡片聚合 + lineage 摘要）
export type {
  ModelArtifactType,
  ModelArtifactStatus,
  DatasetReadmeScope,
  MetricHistoryEntry,
  ModelArtifact,
  DatasetReadme,
  LineageSummary,
  DatasetCard,
  ModelCard,
  UpsertDatasetReadmeRequest,
  UpsertDatasetReadmeResponse,
  RegisterModelRequest,
  RegisterModelResponse,
  UpdateModelRequest,
  UpdateModelResponse,
  DeleteModelResponse,
  AppendModelMetricsRequest,
  AppendModelMetricsResponse,
  ListModelsParams,
  ListModelsResponse,
  GetDatasetCardParams,
  GetModelCardParams,
  GetLineageSummaryParams,
  IResourceCardService,
} from './resource_card';

export {
  MODEL_ARTIFACT_TYPES,
  MODEL_ARTIFACT_TYPE_VALUES,
  MODEL_ARTIFACT_TYPE_LABELS,
  MODEL_ARTIFACT_STATUS,
  MODEL_ARTIFACT_STATUS_VALUES,
  MODEL_ARTIFACT_STATUS_LABELS,
  MODEL_ARTIFACT_STATUS_TAG_TYPE,
  VALID_MODEL_STATUS_TRANSITIONS,
  DATASET_README_SCOPES,
  DATASET_README_SCOPE_VALUES,
  DATASET_README_SCOPE_LABELS,
  isValidSemver,
  readmeScopeFromVersion,
  isModelArtifactType,
  isModelArtifactStatus,
  canTransitionTo,
} from './resource_card';

// Project Package 契约（ADR-015 阶段 6 p6-4：项目导入导出 .lomo 包格式）
export type {
  PackageFormatVersion,
  ContentPolicy,
  ConflictStrategy,
  PackageTaskStatus,
  ImportAction,
  PackageResourceMetadata,
  PackageResourceEntry,
  SourceMachineInfo,
  PackageProjectInfo,
  PackageManifest,
  ExportOptions,
  ImportOptions,
  ExportResult,
  ImportResourceRecord,
  ImportResult,
  ValidationResult,
  ExportProjectRequest,
  ExportProjectResponse,
  ImportProjectParams,
  ImportProjectResponse,
  ValidatePackageResponse,
  PreviewPackageResponse,
  ListExportsParams,
  ExportRecordSummary,
  ListExportsResponse,
  GetExportResponse,
  DeleteExportResponse,
  ListImportsParams,
  ImportRecordSummary,
  ListImportsResponse,
  IProjectPackageService,
} from './project_package';

export {
  PACKAGE_FORMAT_VERSION,
  PACKAGE_FORMAT_VERSION_VALUES,
  PACKAGE_FORMAT_VERSION_LABELS,
  isPackageFormatVersionSupported,
  isPackageFormatVersionMajorCompatible,
  CONTENT_POLICY,
  CONTENT_POLICY_VALUES,
  CONTENT_POLICY_LABELS,
  DEFAULT_CONTENT_POLICY,
  isContentPolicy,
  CONFLICT_STRATEGY,
  CONFLICT_STRATEGY_VALUES,
  CONFLICT_STRATEGY_LABELS,
  CONFLICT_STRATEGY_TAG_TYPE,
  DEFAULT_CONFLICT_STRATEGY,
  isConflictStrategy,
  PACKAGE_TASK_STATUS,
  PACKAGE_TASK_STATUS_VALUES,
  PACKAGE_TASK_STATUS_LABELS,
  PACKAGE_TASK_STATUS_TAG_TYPE,
  TERMINAL_PACKAGE_TASK_STATUS,
  isPackageTaskStatus,
  isTerminalPackageTaskStatus,
  DEFAULT_MAX_FILE_SIZE_BYTES,
  STREAM_BUFFER_SIZE,
  PACKAGE_FILE_EXTENSION,
  PACKAGE_FILENAME_TEMPLATE,
  SOURCE_MACHINE_INFO_DEFAULTS,
  entryHasContent,
  IMPORT_ACTION_VALUES,
  IMPORT_ACTION_LABELS,
  IMPORT_ACTION_TAG_TYPE,
  isImportPartialFailure,
} from './project_package';

// Explainability 契约（ADR-016 阶段 7 p7-5：隐状态投影 + 门控动力学 + 反事实 + 置信度 + 对比）
export type {
  ExplanationType,
  ProjectionMethod,
  ComparisonType,
  ConfidencePercentiles,
  ConfidenceHistogram,
  HiddenStateExplanation,
  GateDynamicsExplanation,
  CounterfactualExplanation,
  ConfidenceExplanation,
  ExplanationPayload,
  ExplanationMetadata,
  ExplanationRecord,
  ExplanationRecordDetail,
  ExplanationComparison,
  GenerateHiddenStateRequest,
  GenerateHiddenStateResponse,
  GenerateGateDynamicsRequest,
  GenerateGateDynamicsResponse,
  GenerateCounterfactualRequest,
  GenerateCounterfactualResponse,
  GenerateConfidenceRequest,
  GenerateConfidenceResponse,
  ListExplanationsParams,
  ListExplanationsResponse,
  GetExplanationParams,
  GetExplanationResponse,
  DeleteExplanationResponse,
  CompareExplanationsRequest,
  CompareExplanationsResponse,
  ExplainabilityErrorCode,
  IExplainabilityService,
} from './explainability';

export {
  EXPLANATION_TYPE,
  EXPLANATION_TYPE_VALUES,
  EXPLANATION_TYPE_LABELS,
  EXPLANATION_TYPE_TAG_TYPE,
  isExplanationType,
  PROJECTION_METHOD,
  PROJECTION_METHOD_VALUES,
  PROJECTION_METHOD_LABELS,
  DEFAULT_PROJECTION_METHOD,
  DEFAULT_PROJECTION_DIM,
  DEFAULT_MAX_FRAMES,
  DEFAULT_ANOMALY_SIGMA,
  DEFAULT_PERTURBATION_STEP,
  DEFAULT_SAMPLE_COUNT,
  isProjectionMethod,
  COMPARISON_TYPE,
  COMPARISON_TYPE_VALUES,
  COMPARISON_TYPE_LABELS,
  COMPARISON_TYPE_TAG_TYPE,
  DEFAULT_COMPARISON_TYPE,
  isComparisonType,
  EXPLAINABILITY_ERROR_CODE,
  EXPLAINABILITY_ERROR_CODE_VALUES,
  EXPLAINABILITY_ERROR_CODE_LABELS,
  isExplainabilityErrorCode,
  CONTRACTS_EXPLAINABILITY_VERSION,
} from './explainability';

// World Model 契约（ADR-017 阶段 8 p8-6：轨迹预测 + 版本管理）
export type {
  StateField,
  ActionField,
  WorldModelPredictRequest,
  TrajectoryStep,
  TrajectoryMetrics,
  WorldModelInfo,
  WorldModelPredictResponse,
  WorldModelVersion,
  ListWorldModelVersionsParams,
  ListWorldModelVersionsResponse,
  WorldModelErrorCode,
  IWorldModelService,
} from './world_model';

export {
  WM_PREDICT_STATE_TASK_TYPE,
  DEFAULT_STATE_DIM,
  DEFAULT_ACTION_DIM,
  DEFAULT_HORIZON,
  MAX_HORIZON,
  MIN_HORIZON,
  DEFAULT_WORLD_MODEL_URI,
  STATE_FIELD,
  STATE_FIELD_VALUES,
  STATE_FIELD_LABELS,
  isStateField,
  ACTION_FIELD,
  ACTION_FIELD_VALUES,
  ACTION_FIELD_LABELS,
  isActionField,
  WORLD_MODEL_ERROR_CODE,
  WORLD_MODEL_ERROR_CODE_VALUES,
  WORLD_MODEL_ERROR_CODE_LABELS,
  WORLD_MODEL_ERROR_CODE_TAG_TYPE,
  isWorldModelErrorCode,
  CONTRACTS_WORLD_MODEL_VERSION,
} from './world_model';

// RL Agent 契约（ADR-017 阶段 8 p8-6：决策推理 + 训练控制）
export type {
  OptimizationTarget,
  PolicyAlgorithm,
  TrainingStatus,
  SafetyConstraintsSpec,
  RLActRequest,
  ActionEvaluation,
  PolicyInfo,
  RecommendedAction,
  RLActResponse,
  PolicyVersion,
  TrainingMetricsSnapshot,
  TrainingStatusInfo,
  TrainingStartRequest,
  ListPolicyVersionsParams,
  ListPolicyVersionsResponse,
  RLAgentErrorCode,
  IRLAgentService,
} from './rl_agent';

export {
  RL_ACT_TASK_TYPE,
  OPTIMIZATION_TARGET,
  OPTIMIZATION_TARGET_VALUES,
  OPTIMIZATION_TARGET_LABELS,
  OPTIMIZATION_TARGET_TAG_TYPE,
  DEFAULT_OPTIMIZATION_TARGET,
  isOptimizationTarget,
  POLICY_ALGORITHM,
  POLICY_ALGORITHM_VALUES,
  POLICY_ALGORITHM_LABELS,
  POLICY_ALGORITHM_TAG_TYPE,
  DEFAULT_POLICY_ALGORITHM,
  isPolicyAlgorithm,
  TRAINING_STATUS,
  TRAINING_STATUS_VALUES,
  TRAINING_STATUS_LABELS,
  TRAINING_STATUS_TAG_TYPE,
  TERMINAL_TRAINING_STATUS,
  isTrainingStatus,
  isTerminalTrainingStatus,
  DEFAULT_RL_AGENT_URI,
  DEFAULT_MAX_STEPS,
  MIN_MAX_STEPS,
  MAX_MAX_STEPS,
  DEFAULT_SAFETY_CONSTRAINTS,
  RL_AGENT_ERROR_CODE,
  RL_AGENT_ERROR_CODE_VALUES,
  RL_AGENT_ERROR_CODE_LABELS,
  RL_AGENT_ERROR_CODE_TAG_TYPE,
  isRLAgentErrorCode,
  CONTRACTS_RL_AGENT_VERSION,
} from './rl_agent';

// 契约版本总览

/** 五大契约统一版本号（与后端 CONTRACTS_VERSION 对齐）。 */
export const CONTRACTS_VERSION = '1.0.0';

/** 各契约子版本（当前都为 1.0.0，未来可独立演进）。 */
export const CONTRACT_VERSIONS = {
  task: '1.0.0',
  dataset: '1.0.0',
  plugin: '1.0.0',
  config: '1.0.0',
  observability: '1.0.0',
} as const;

/** 契约清单（用于前端运行时校验与文档生成）。 */
export const CONTRACT_NAMES = [
  'task',
  'dataset',
  'plugin',
  'config',
  'observability',
] as const;

export type ContractName = (typeof CONTRACT_NAMES)[number];
