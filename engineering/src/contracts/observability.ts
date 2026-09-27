/**
 * 可观测契约（Observability Contract）
 *
 * 对应后端 app/contracts/observability.py。
 * 详见 docs/development/core-contracts-design.md 第 7 章。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

/** 结构化日志级别（与 Python logging 标准对齐）。 */
export type LogLevel = 'debug' | 'info' | 'warning' | 'error' | 'critical';

export const LOG_LEVELS: LogLevel[] = [
  'debug',
  'info',
  'warning',
  'error',
  'critical',
];

/** 合法的 span 状态。 */
export type SpanStatus = 'ok' | 'error';

export const VALID_SPAN_STATUSES: SpanStatus[] = ['ok', 'error'];

/** trace span 契约。 */
export interface TraceSpan {
  /** span 唯一 ID（建议 UUID 或 16-hex）。 */
  span_id: string;
  /** 所属 trace 的 ID（同一请求/工作流共享）。 */
  trace_id: string;
  /** 父 span ID（根 span 为 null）。 */
  parent_span_id?: string;
  /** span 名称（如 "ltc.train.epoch"）。 */
  name: string;
  /** 起始 Unix 时间戳（秒）。 */
  start_ts: number;
  /** 结束 Unix 时间戳（秒）；未结束时为 null。 */
  end_ts?: number;
  /** 业务属性（任意可序列化字典）。 */
  attributes: Record<string, unknown>;
  /** 事件列表，每项 { name, ts, payload }。 */
  events: Array<{
    name: string;
    ts: number;
    payload: Record<string, unknown>;
  }>;
  /** 状态："ok" 或 "error"。 */
  status: SpanStatus;
}

/** metric 契约。 */
export interface Metric {
  /** 指标名（如 "ltc.train.loss"）。 */
  name: string;
  /** 指标值（数值类型）。 */
  value: number;
  /** Unix 时间戳（秒）。 */
  timestamp: number;
  /** 标签字典（Prometheus 风格，如 { fold: "1", epoch: "3" }）。 */
  labels?: Record<string, string>;
  /** 单位（如 "ms"/"loss"/"accuracy"）。 */
  unit?: string;
}

/** 结构化日志契约。 */
export interface LogEntry {
  /** Unix 时间戳（秒）。 */
  timestamp: number;
  /** 日志级别。 */
  level: LogLevel;
  /** 日志消息。 */
  message: string;
  /** logger 名（通常为模块路径）。 */
  logger?: string;
  /** 附加属性（任意可序列化字典）。 */
  attributes?: Record<string, unknown>;
  /** 关联 trace ID（用于日志关联追踪）。 */
  trace_id?: string;
  /** 关联 span ID。 */
  span_id?: string;
}

/** 实验快照契约（一键复现的最小单元）。 */
export interface ExperimentSnapshot {
  /** 快照唯一 ID。 */
  snapshot_id: string;
  /** 创建时间（ISO 8601）。 */
  created_at: string;
  /** 创建者（用户 ID 或 "system"）。 */
  created_by: string;
  /** 代码 git commit SHA。 */
  git_sha: string;
  /** 是否有未提交修改（true 时复现结果不可保证）。 */
  code_dirty: boolean;
  /** 完整实验配置（已 materialize 的字典）。 */
  config: Record<string, unknown>;
  /** 数据集版本列表，形如 ["dataset://xxx/v1"]。 */
  dataset_versions: string[];
  /** 模型 URI，形如 "model://ltc-v1"。 */
  model_uri: string;
  /** 关键指标字典。 */
  metrics: Record<string, number>;
  /** 环境信息（python 版本/关键包版本）。 */
  environment: Record<string, string>;
  /** 关联的血缘记录 ID。 */
  lineage_record_id?: string;
  /** 关联 MLflow run ID（可选）。 */
  mlflow_run_id?: string;
  /** 备注。 */
  notes?: string;
}

/** 实验快照创建参数（前端 → 后端，省略自动采集字段）。 */
export interface CreateSnapshotParams {
  config: Record<string, unknown>;
  dataset_versions: string[];
  model_uri: string;
  metrics: Record<string, number>;
  created_by: string;
  notes?: string;
}

/** 列出快照的过滤条件。 */
export interface SnapshotFilters {
  created_by?: string;
  git_sha?: string;
  model_uri?: string;
  /** 时间范围（Unix 时间戳，秒）。 */
  since?: number;
  until?: number;
}

// 抽象接口

/**
 * trace sink 契约。
 *
 * 实现方负责管理 span 的生命周期与持久化。线程安全要求由实现方保证。
 */
export interface ITraceSink {
  /**
   * 开启一个 span，返回 span_id。
   *
   * @param name span 名称
   * @param parent 父 span ID；null/undefined 表示根 span
   * @returns 新 span 的 ID
   */
  start_span(name: string, parent?: string): string | Promise<string>;

  /**
   * 结束一个 span。
   *
   * @param span_id span ID
   * @param status "ok" 或 "error"
   */
  end_span(span_id: string, status?: SpanStatus): void | Promise<void>;

  /** 为 span 添加属性。 */
  add_attribute(span_id: string, key: string, value: unknown): void | Promise<void>;

  /** 为 span 添加事件。 */
  add_event(
    span_id: string,
    name: string,
    payload: Record<string, unknown>,
  ): void | Promise<void>;
}

/**
 * metric sink 契约。
 *
 * 实现方负责把指标推送到后端（Prometheus / MLflow / 文件）。
 */
export interface IMetricSink {
  /** 递增计数器。 */
  counter(name: string, value?: number, labels?: Record<string, string>): void | Promise<void>;

  /** 设置 gauge 当前值。 */
  gauge(name: string, value: number, labels?: Record<string, string>): void | Promise<void>;

  /** 记录 histogram 样本。 */
  histogram(name: string, value: number, labels?: Record<string, string>): void | Promise<void>;
}

/**
 * log sink 契约。
 *
 * 实现方负责把日志写入后端（文件 / stdout / 远程日志服务）。
 * 必须实现敏感数据脱敏（与 LogSanitizer 集成）。
 */
export interface ILogSink {
  /** 写入一条结构化日志。 */
  log(entry: LogEntry): void | Promise<void>;
}

/**
 * 实验快照存储契约。
 *
 * 实现方负责：
 * - 自动采集 git_sha / environment
 * - 持久化 snapshot 到数据库 / 文件
 * - 提供 reproduce 入口（与 IWorkflowRunner 集成）
 */
export interface ISnapshotStore {
  /**
   * 创建快照，自动采集 git_sha / environment，写入存储。
   *
   * @returns 已持久化的 ExperimentSnapshot（含 snapshot_id）
   */
  create(params: CreateSnapshotParams): Promise<ExperimentSnapshot>;

  /** 按 ID 取快照，不存在抛 Error。 */
  get(snapshot_id: string): Promise<ExperimentSnapshot>;

  /** 列出快照，可选过滤（按 created_at 降序）。 */
  list(filters?: SnapshotFilters): Promise<ExperimentSnapshot[]>;

  /**
   * 根据 snapshot 恢复环境并启动复现任务。
   *
   * @returns workflow_run_id（复现工作流的运行 ID）
   */
  reproduce(snapshot_id: string): Promise<string>;
}

/**
 * 可观测统一入口。
 *
 * 业务模块通过此接口埋点，无需关心后端。实现方通常组合多个独立 sink，
 * 例如 CompositeObservabilitySink。
 *
 * 这是契约层，不提供默认实现。
 */
export interface IObservabilitySink
  extends ITraceSink,
    IMetricSink,
    ILogSink,
    ISnapshotStore {}

export const CONTRACTS_OBSERVABILITY_VERSION = '1.0.0';
