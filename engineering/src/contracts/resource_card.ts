/**
 * 资源卡片契约（Resource Card Contract）
 *
 * 对应后端 app/contracts/resource_card.py。
 * 详见 docs/adr/ADR-012-资源卡片.md。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 *
 * 设计要点：
 *   1. 不修改现有 Dataset / DatasetVersion / LineageRecord 契约（ADR-005 Stable），
 *      新增 dataset_readmes 表承载可编辑 README，新增 model_artifacts 表承载模型产物元数据
 *   2. ModelArtifact 通过 model_uri（model://<name>/<version>）与 ADR-011 项目同步对齐
 *   3. LineageSummary 不返回全图，而是按层分组（BFS，每层最多 10 节点）+ 关键路径，
 *      避免卡片渲染压力；需要全图时调用 ILineageStore.visualize()
 *   4. DatasetCard / ModelCard 是聚合视图，由服务层调用 IDatasetStore / ILineageStore /
 *      ISnapshotStore 拼接，前端单次请求获取完整卡片
 *   5. ModelArtifactStatus 状态机与 DatasetStatus 对齐（draft/published/deprecated/archived）
 *
 * 资源 URI 体系（与 ADR-005 / ADR-011 对齐）：
 *   model://<model_name>/<version>
 *   dataset://<dataset_id>/<version>
 */

// 模型产物类型常量（与后端 ModelArtifactType 对齐）

/** 模型产物类型。决定模型文件的加载方式与框架依赖。 */
export const MODEL_ARTIFACT_TYPES = {
  LNN: 'lnn',
  PYTORCH: 'pytorch',
  ONNX: 'onnx',
  SKLEARN: 'sklearn',
  OTHER: 'other',
} as const;

export type ModelArtifactType =
  (typeof MODEL_ARTIFACT_TYPES)[keyof typeof MODEL_ARTIFACT_TYPES];

/** 所有模型产物类型列表（用于 UI 选择器渲染）。 */
export const MODEL_ARTIFACT_TYPE_VALUES: readonly ModelArtifactType[] = [
  MODEL_ARTIFACT_TYPES.LNN,
  MODEL_ARTIFACT_TYPES.PYTORCH,
  MODEL_ARTIFACT_TYPES.ONNX,
  MODEL_ARTIFACT_TYPES.SKLEARN,
  MODEL_ARTIFACT_TYPES.OTHER,
] as const;

/** 模型产物类型中文标签（用于 UI 展示）。 */
export const MODEL_ARTIFACT_TYPE_LABELS: Record<ModelArtifactType, string> = {
  lnn: '液态神经网络（LNN）',
  pytorch: 'PyTorch 模型',
  onnx: 'ONNX 模型',
  sklearn: 'scikit-learn 模型',
  other: '其他格式',
};

// 模型产物状态常量（与后端 ModelArtifactStatus 对齐）

/**
 * 模型产物状态机。
 *
 * 状态转换：
 *   DRAFT → PUBLISHED（不可变，发布后内容固定）
 *   PUBLISHED → DEPRECATED → ARCHIVED
 *   DRAFT → ARCHIVED（直接归档未发布模型）
 *
 * PUBLISHED 状态的模型可被生产环境引用；DEPRECATED 仍可推理但不推荐新用途；
 * ARCHIVED 仅保留历史记录，不参与推理。
 */
export const MODEL_ARTIFACT_STATUS = {
  DRAFT: 'draft',
  PUBLISHED: 'published',
  DEPRECATED: 'deprecated',
  ARCHIVED: 'archived',
} as const;

export type ModelArtifactStatus =
  (typeof MODEL_ARTIFACT_STATUS)[keyof typeof MODEL_ARTIFACT_STATUS];

/** 所有模型产物状态列表。 */
export const MODEL_ARTIFACT_STATUS_VALUES: readonly ModelArtifactStatus[] = [
  MODEL_ARTIFACT_STATUS.DRAFT,
  MODEL_ARTIFACT_STATUS.PUBLISHED,
  MODEL_ARTIFACT_STATUS.DEPRECATED,
  MODEL_ARTIFACT_STATUS.ARCHIVED,
] as const;

/** 模型产物状态中文标签（用于 UI 状态徽章渲染）。 */
export const MODEL_ARTIFACT_STATUS_LABELS: Record<ModelArtifactStatus, string> = {
  draft: '草稿',
  published: '已发布',
  deprecated: '已弃用',
  archived: '已归档',
};

/** 模型产物状态对应的 UI 颜色类型（与 element-plus tag type 对齐）。 */
export const MODEL_ARTIFACT_STATUS_TAG_TYPE: Record<
  ModelArtifactStatus,
  'success' | 'warning' | 'info' | 'danger'
> = {
  draft: 'info',
  published: 'success',
  deprecated: 'warning',
  archived: 'danger',
};

/**
 * 合法状态转换表（与后端 VALID_MODEL_STATUS_TRANSITIONS 对齐）。
 *
 * key 为当前状态，value 为允许转换的目标状态集合。
 * PUBLISHED 之后内容不可变，只能改状态（deprecated / archived）。
 */
export const VALID_MODEL_STATUS_TRANSITIONS: Record<
  ModelArtifactStatus,
  readonly ModelArtifactStatus[]
> = {
  draft: [MODEL_ARTIFACT_STATUS.PUBLISHED, MODEL_ARTIFACT_STATUS.ARCHIVED],
  published: [MODEL_ARTIFACT_STATUS.DEPRECATED, MODEL_ARTIFACT_STATUS.ARCHIVED],
  deprecated: [MODEL_ARTIFACT_STATUS.ARCHIVED],
  archived: [],
};

// 数据集 README 作用域常量（与后端 DatasetReadmeScope 对齐）

/**
 * 数据集 README 作用域。
 *
 * 决定 dataset_readmes.version 字段的语义：
 *   - DATASET_LEVEL: version=null，表示整个数据集的 README（默认展示）
 *   - VERSION_LEVEL: version="1.0.0"，表示特定版本的 README（覆盖数据集级）
 */
export const DATASET_README_SCOPES = {
  DATASET_LEVEL: 'dataset_level',
  VERSION_LEVEL: 'version_level',
} as const;

export type DatasetReadmeScope =
  (typeof DATASET_README_SCOPES)[keyof typeof DATASET_README_SCOPES];

/** 所有 README 作用域列表。 */
export const DATASET_README_SCOPE_VALUES: readonly DatasetReadmeScope[] = [
  DATASET_README_SCOPES.DATASET_LEVEL,
  DATASET_README_SCOPES.VERSION_LEVEL,
] as const;

/** README 作用域中文标签。 */
export const DATASET_README_SCOPE_LABELS: Record<DatasetReadmeScope, string> = {
  dataset_level: '数据集级',
  version_level: '版本级',
};

// 工具函数（与后端 _is_valid_semver / DatasetReadmeScope.from_version 对齐）

/**
 * 校验 semver 格式：MAJOR.MINOR.PATCH（可选 -prerelease）.
 *
 * 与后端 _is_valid_semver 对齐，prerelease 段可含字母与点号。
 */
export function isValidSemver(version: string): boolean {
  if (!version) return false;
  let main: string;
  if (version.includes('-')) {
    const idx = version.indexOf('-');
    main = version.slice(0, idx);
    const prerelease = version.slice(idx + 1);
    if (!prerelease) return false;
  } else {
    main = version;
  }
  const parts = main.split('.');
  if (parts.length !== 3) return false;
  return parts.every((p) => /^\d+$/.test(p));
}

/** 根据 version 字段推断 README 作用域。null/undefined → DATASET_LEVEL。 */
export function readmeScopeFromVersion(
  version: string | null | undefined,
): DatasetReadmeScope {
  return version ? DATASET_README_SCOPES.VERSION_LEVEL : DATASET_README_SCOPES.DATASET_LEVEL;
}

/** 类型守卫：判断字符串是否为合法 ModelArtifactType. */
export function isModelArtifactType(
  value: string,
): value is ModelArtifactType {
  return MODEL_ARTIFACT_TYPE_VALUES.includes(value as ModelArtifactType);
}

/** 类型守卫：判断字符串是否为合法 ModelArtifactStatus. */
export function isModelArtifactStatus(
  value: string,
): value is ModelArtifactStatus {
  return MODEL_ARTIFACT_STATUS_VALUES.includes(value as ModelArtifactStatus);
}

/** 检查状态转换是否合法（与后端 ModelArtifact.can_transition_to 对齐）。 */
export function canTransitionTo(
  current: ModelArtifactStatus,
  target: ModelArtifactStatus,
): boolean {
  return VALID_MODEL_STATUS_TRANSITIONS[current].includes(target);
}

// 数据接口：模型产物

/** 指标历史条目（追加式记录，每项含 timestamp + metrics）。 */
export interface MetricHistoryEntry {
  /** ISO 8601 时间字符串。 */
  timestamp: string;
  /** 指标字典，如 { accuracy: 0.95, loss: 0.05 }。 */
  metrics: Record<string, unknown>;
}

/**
 * 模型产物契约.
 *
 * 持久化到 model_artifacts 表，承载模型元数据 + 指标 + README + 标签。
 * model_uri 是唯一标识（model://<name>/<version>），与 ADR-011 项目同步对齐。
 */
export interface ModelArtifact {
  /** 模型 ID（mdl_ 前缀 + uuid）。 */
  model_id: string;
  /** 模型 URI（model://<name>/<version>），全局唯一。 */
  model_uri: string;
  /** 模型显示名（如 "LTC-ChatterPredictor"）。 */
  name: string;
  /** 模型类型（ModelArtifactType 常量）。 */
  model_type: ModelArtifactType;
  /** semver 版本号，如 "1.0.0"。 */
  version: string;
  /** 框架版本，如 "torch-2.1.0"。 */
  framework: string;
  /** 模型文件存储位置（file:// / s3:// 路径）。 */
  storage_uri: string;
  /** 所有者 user_id 或 plugin_id。 */
  owner_id: string;
  /** 模型状态。 */
  status: ModelArtifactStatus;
  /** 当前指标快照，如 { accuracy: 0.95, loss: 0.05 }。 */
  metrics: Record<string, unknown>;
  /** 指标历史（追加式记录，按时间升序）。 */
  metrics_history: MetricHistoryEntry[];
  /** markdown README。 */
  readme_md: string;
  /** 标签数组。 */
  tags: string[];
  /** 创建时间（ISO 8601）。 */
  created_at: string | null;
  /** 最后更新时间（ISO 8601）。 */
  updated_at: string | null;
}

// 数据接口：数据集 README

/**
 * 数据集 README 契约.
 *
 * 持久化到 dataset_readmes 表，支持数据集级（version=null）与版本级（version="1.0.0"）README。
 * 版本级 README 覆盖数据集级，前端展示时优先取版本级，回退到数据集级。
 */
export interface DatasetReadme {
  /** README ID（readme_ 前缀 + uuid）。 */
  readme_id: string;
  /** 关联 datasets.id。 */
  dataset_id: string;
  /** 版本号（null 表示数据集级 README）。 */
  version: string | null;
  /** README 作用域（由 version 推导）。 */
  scope: DatasetReadmeScope;
  /** markdown README 内容。 */
  readme_md: string;
  /** 最后更新者 user_id。 */
  updated_by: string;
  /** 最后更新时间（ISO 8601）。 */
  updated_at: string | null;
}

// 数据接口：血缘摘要

/**
 * 血缘摘要契约.
 *
 * 卡片视图的轻量血缘概览，避免全图渲染压力。
 *
 * 字段说明：
 *   - upstream_count / downstream_count：全量计数（不限 depth）
 *   - upstream_layers / downstream_layers：按层分组的节点 URI（BFS），
 *     每层最多 10 个节点，超出部分仅在 count 中体现
 *   - key_path：target 到根节点的最短路径（用于卡片侧栏展示）
 *   - total_nodes：上游 + 下游 + target 自身的总节点数
 */
export interface LineageSummary {
  /** 卡片目标的资源 URI。 */
  target_uri: string;
  /** 上游节点总数（全量，不限 depth）。 */
  upstream_count: number;
  /** 下游节点总数（全量，不限 depth）。 */
  downstream_count: number;
  /** 上游按层分组的节点 URI（BFS，[[layer1_uris], [layer2_uris], ...]）。 */
  upstream_layers: string[][];
  /** 下游按层分组的节点 URI。 */
  downstream_layers: string[][];
  /** target → 根的最短路径。 */
  key_path: string[];
  /** 总节点数（含 target）。 */
  total_nodes: number;
}

// 数据接口：数据集卡片（聚合视图）

/**
 * 数据集卡片契约（聚合视图）.
 *
 * 由后端 ResourceCardService.get_dataset_card() 聚合以下数据源拼接而成：
 *   - dataset：IDatasetStore.get_dataset() 返回的元数据
 *   - latest_version：IDatasetStore.list_versions() 的最新版本
 *   - version_count / total_rows / total_size：从版本列表汇总
 *   - readme：DatasetReadme（优先版本级，回退数据集级，再回退 description）
 *   - lineage_summary：LineageSummary（target_uri = dataset://<id>/<version>）
 */
export interface DatasetCard {
  /** 数据集 ID。 */
  dataset_id: string;
  /** 数据集显示名。 */
  name: string;
  /** 原始 description（短文本）。 */
  description: string;
  /** 所有者 user_id 或 plugin_id。 */
  owner_id: string;
  /** 数据集状态（DatasetStatus 值）。 */
  status: 'draft' | 'published' | 'deprecated' | 'archived';
  /** 数据集 schema（DatasetSchema 序列化）。 */
  schema: Record<string, unknown>;
  /** 版本总数。 */
  version_count: number;
  /** 所有版本累计行数。 */
  total_rows: number;
  /** 所有版本累计字节数。 */
  total_size_bytes: number;
  /** 最新版本元数据（无版本时为 null）。 */
  latest_version: Record<string, unknown> | null;
  /** README（null 表示未设置）。 */
  readme: DatasetReadme | null;
  /** 血缘摘要（include_lineage=false 时为 null）。 */
  lineage_summary: LineageSummary | null;
  /** 创建时间（ISO 8601）。 */
  created_at: string | null;
  /** 最后更新时间（ISO 8601）。 */
  updated_at: string | null;
}

// 数据接口：模型卡片（聚合视图）

/**
 * 模型卡片契约（聚合视图）.
 *
 * 由后端 ResourceCardService.get_model_card() 聚合以下数据源拼接而成：
 *   - model_artifact：ModelArtifact 元数据
 *   - snapshot_count：关联该 model_uri 的 ExperimentSnapshot 数量
 *   - lineage_summary：LineageSummary（target_uri = model_uri）
 *   - metrics_history：从 model_artifact.metrics_history 取出，按时间排序
 *   - latest_snapshot：最近一次实验快照摘要
 */
export interface ModelCard {
  /** 模型产物元数据。 */
  model: ModelArtifact;
  /** 关联该模型的实验快照数。 */
  snapshot_count: number;
  /** 血缘摘要（include_lineage=false 时为 null）。 */
  lineage_summary: LineageSummary | null;
  /** 最近一次快照摘要（无快照时为 null）。 */
  latest_snapshot: Record<string, unknown> | null;
}

// 请求 / 响应接口：数据集 README upsert

/** 更新数据集 README 请求体（upsert 语义，对应 PUT /datasets/{id}/readme）。 */
export interface UpsertDatasetReadmeRequest {
  /** markdown README 内容（1-200000 字符）。 */
  readme_md: string;
  /** 最后更新者（user_id 或 plugin_id，1-128 字符）。 */
  updated_by: string;
  /** 版本号（如 "1.0.0"），不传则更新数据集级 README。 */
  version?: string | null;
}

/** upsert README 响应（返回最新 README）。 */
export interface UpsertDatasetReadmeResponse extends DatasetReadme {}

// 请求 / 响应接口：模型产物 CRUD

/** 注册新模型产物请求体（对应 POST /models）。 */
export interface RegisterModelRequest {
  /** 模型 URI（model://<name>/<version>），全局唯一。 */
  model_uri: string;
  /** 模型显示名。 */
  name: string;
  /** 模型类型（ModelArtifactType 常量）。 */
  model_type: ModelArtifactType;
  /** semver 版本号（如 "1.0.0"）。 */
  version: string;
  /** 框架版本（如 "torch-2.1.0"）。 */
  framework: string;
  /** 模型文件存储位置。 */
  storage_uri: string;
  /** 所有者 ID。 */
  owner_id: string;
  /** markdown README（可空）。 */
  readme_md?: string;
  /** 标签数组。 */
  tags?: string[];
  /** 初始指标快照（如 { accuracy: 0.95, loss: 0.05 }）。 */
  metrics?: Record<string, unknown>;
  /** 初始状态（默认 draft）。 */
  status?: ModelArtifactStatus;
}

/** 注册模型响应（返回完整 ModelArtifact）。 */
export interface RegisterModelResponse extends ModelArtifact {}

/** 更新模型卡片请求体（部分更新，仅非 undefined 字段被写入）。 */
export interface UpdateModelRequest {
  /** markdown README。 */
  readme_md?: string;
  /** 标签数组。 */
  tags?: string[];
  /** 目标状态（受状态机约束）。 */
  status?: ModelArtifactStatus;
  /** 覆盖当前指标快照（不会追加到 history，请用 POST /metrics 追加）。 */
  metrics?: Record<string, unknown>;
  /** 框架版本。 */
  framework?: string;
  /** 模型文件存储位置。 */
  storage_uri?: string;
}

/** 更新模型响应（返回更新后的 ModelArtifact）。 */
export interface UpdateModelResponse extends ModelArtifact {}

/** 删除模型响应。 */
export interface DeleteModelResponse {
  /** 模型 ID。 */
  model_id: string;
  /** 是否已删除。 */
  deleted: boolean;
}

// 请求 / 响应接口：模型指标追加

/** 追加模型指标记录请求体（对应 POST /models/{id}/metrics）。 */
export interface AppendModelMetricsRequest {
  /** 指标字典（如 { accuracy: 0.95, loss: 0.05 }）。 */
  metrics: Record<string, unknown>;
  /** 自定义时间戳（ISO 8601），不传则使用服务器当前时间。 */
  timestamp?: string;
}

/** 追加指标响应（返回更新后的 ModelArtifact，含新的 metrics_history）。 */
export interface AppendModelMetricsResponse extends ModelArtifact {}

// 请求 / 响应接口：模型列表查询

/** 列出模型查询参数。 */
export interface ListModelsParams {
  /** 按模型类型过滤。 */
  model_type?: ModelArtifactType;
  /** 按状态过滤。 */
  status?: ModelArtifactStatus;
  /** 按所有者过滤。 */
  owner_id?: string;
  /** 按标签过滤（精确匹配单个标签）。 */
  tag?: string;
  /** 按名称模糊搜索。 */
  name?: string;
  /** 分页大小（1-1000，默认 100）。 */
  limit?: number;
  /** 分页偏移（>=0）。 */
  offset?: number;
}

/** 列出模型响应。 */
export interface ListModelsResponse {
  /** 模型产物列表。 */
  items: ModelArtifact[];
  /** 总数。 */
  total: number;
  /** 当前分页大小。 */
  limit: number;
  /** 当前分页偏移。 */
  offset: number;
}

// 请求 / 响应接口：卡片聚合查询

/** 获取数据集卡片查询参数。 */
export interface GetDatasetCardParams {
  /** 是否包含 lineage 摘要（默认 true）。 */
  include_lineage?: boolean;
  /** lineage 摘要深度（1-10，默认 3）。 */
  lineage_depth?: number;
}

/** 获取模型卡片查询参数。 */
export interface GetModelCardParams {
  /** 是否包含 lineage 摘要（默认 true）。 */
  include_lineage?: boolean;
  /** lineage 摘要深度（1-10，默认 3）。 */
  lineage_depth?: number;
}

/** 获取 lineage 摘要查询参数（独立端点）。 */
export interface GetLineageSummaryParams {
  /** 最大深度（1-10，默认 3）。 */
  max_depth?: number;
  /** 每层最多节点数（1-100，默认 10）。 */
  max_nodes_per_layer?: number;
}

// 抽象接口（前端通过 HTTP 调用后端实现）

/**
 * 资源卡片聚合服务契约（接口占位，便于插件扩展与测试 mock）.
 *
 * 实现见后端 app/services/resource_card_service.py。
 * 前端通过 useResourceCardStore（Pinia）调用 REST API 间接使用此契约。
 */
export interface IResourceCardService {
  /** 获取数据集卡片（聚合 Dataset + Version 指标 + README + lineage 摘要）。 */
  getDatasetCard(
    datasetId: string,
    opts?: GetDatasetCardParams,
  ): Promise<DatasetCard>;

  /** 获取模型卡片（聚合 ModelArtifact + Snapshot 数 + lineage 摘要）。 */
  getModelCard(
    modelId: string,
    opts?: GetModelCardParams,
  ): Promise<ModelCard>;

  /** 获取 lineage 摘要（按层分组 + 关键路径）。 */
  getLineageSummary(
    targetUri: string,
    opts?: GetLineageSummaryParams,
  ): Promise<LineageSummary>;
}
