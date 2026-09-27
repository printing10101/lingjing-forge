/**
 * 数据契约（Dataset & Version & Lineage Contract）
 *
 * 对应后端 app/contracts/dataset.py。
 * 详见 docs/development/core-contracts-design.md 第 4 章。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

/** 数据集状态。DRAFT → PUBLISHED（不可变）→ DEPRECATED / ARCHIVED。 */
export type DatasetStatus = 'draft' | 'published' | 'deprecated' | 'archived';

/** 字段类型（与 Python VALID_FIELD_TYPES 对齐）。 */
export type DatasetFieldType = 'int' | 'float' | 'str' | 'bool' | 'list' | 'dict';

/** 数据集字段定义。 */
export interface DatasetFieldDef {
  type: DatasetFieldType;
  required?: boolean;
  description?: string;
  [k: string]: unknown;
}

/** 数据集 schema。 */
export interface DatasetSchema {
  /** 字段名 → 字段定义。 */
  fields: Record<string, DatasetFieldDef>;
  primary_key: string[];
  metadata: Record<string, unknown>;
}

/** 数据集版本（不可变快照）。 */
export interface DatasetVersion {
  dataset_id: string;
  /** semver，如 "1.0.0"。 */
  version: string;
  status: DatasetStatus;
  schema: DatasetSchema;
  /** sha256，内容寻址。 */
  content_hash: string;
  row_count: number;
  size_bytes: number;
  /** ISO 8601 时间字符串。 */
  created_at: string;
  /** user_id 或 plugin_id。 */
  created_by: string;
  /** 实际存储位置。 */
  storage_uri: string;
  /** lineage record id。 */
  lineage?: string;
}

/** 血缘来源类型。 */
export type LineageSourceType = 'task' | 'workflow' | 'manual' | 'external';

/** 血缘记录。 */
export interface LineageRecord {
  record_id: string;
  /** "dataset://my-ds/v1" / "model://ltc-v1"。 */
  target: string;
  source_type: LineageSourceType;
  /** job_id / workflow_run_id / url。 */
  source_ref: string;
  /** 上游 artifact uri 列表。 */
  inputs: string[];
  outputs: string[];
  /** "train" / "preprocess" / "augment"。 */
  operation: string;
  /** ISO 8601 时间字符串。 */
  timestamp: string;
  metadata: Record<string, unknown>;
}

/** 数据集摘要（列表视图用）。 */
export interface DatasetSummary {
  dataset_id: string;
  name: string;
  description: string;
  owner_id: string;
  current_status: DatasetStatus;
  latest_version?: string;
  version_count: number;
  created_at: string;
  updated_at: string;
}

// 抽象接口（前端通过 HTTP 调用后端实现）

/** 数据集存储接口。 */
export interface IDatasetStore {
  create(
    name: string,
    schema: DatasetSchema,
    opts: { owner_id: string; description?: string },
  ): Promise<string>;
  commitVersion(
    dataset_id: string,
    records: Record<string, unknown>[],
    opts?: { version?: string; lineage?: LineageRecord },
  ): Promise<DatasetVersion>;
  getVersion(dataset_id: string, version: string): Promise<DatasetVersion>;
  read(dataset_id: string, version: string): Promise<Record<string, unknown>[]>;
  listVersions(dataset_id: string): Promise<DatasetVersion[]>;
  deprecate(dataset_id: string, version: string): Promise<void>;
}

/** 血缘存储接口。 */
export interface ILineageStore {
  record(record: LineageRecord): Promise<void>;
  getUpstream(target: string): Promise<LineageRecord[]>;
  getDownstream(target: string): Promise<LineageRecord[]>;
  /** 可视化数据（节点 + 边）。 */
  visualize(target: string): Promise<{ nodes: unknown[]; edges: unknown[] }>;
}

export const CONTRACTS_DATASET_VERSION = '1.0.0';
