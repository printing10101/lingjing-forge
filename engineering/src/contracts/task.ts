/**
 * 任务契约（Task & Workflow Contract）
 *
 * 对应后端 app/contracts/task.py。
 * 详见 docs/development/core-contracts-design.md 第 3 章。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

/** 任务状态。PENDING → QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED；SKIPPED 用于工作流中前置失败时跳过下游节点。 */
export type TaskStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'skipped';

/** 任务优先级（数字越大优先级越高）。 */
export type TaskPriority = 1 | 5 | 8 | 10;

/** 任务产物类型。 */
export type ArtifactType = 'dataset' | 'model' | 'report' | 'metrics' | 'file';

/** 任务输入输出产物。 */
export interface Artifact {
  name: string;
  type: ArtifactType;
  /** 内部 URI，如 "dataset://my-ds/v3" / "model://ltc-v1"。 */
  uri: string;
  metadata: Record<string, unknown>;
}

/** 任务运行时上下文（由编排器注入）。 */
export interface TaskContext {
  job_id: string;
  workflow_run_id?: string;
  inputs: Record<string, Artifact>;
  config: Record<string, unknown>;
  retry_count: number;
  /** Unix 时间戳（秒），超时自动 CANCELLED。 */
  deadline_ts?: number;
}

/** 任务执行结果。 */
export interface TaskResult {
  status: TaskStatus;
  outputs: Record<string, Artifact>;
  metrics: Record<string, number>;
  error?: string;
  error_code?: string;
}

/** 任务进度。 */
export interface TaskProgress {
  job_id: string;
  status: TaskStatus;
  /** 0..1。 */
  progress: number;
  message?: string;
  timestamp: number;
}

/** 任务处理器描述（前端只读视图，对应后端 TaskHandler Protocol）。 */
export interface TaskHandlerDescriptor {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
}

/** DAG 节点：一个任务实例。 */
export interface WorkflowNode {
  node_id: string;
  task_type: string;
  params: Record<string, unknown>;
  /** 形如 { "input_name": "${upstream_node_id.output_name}" }。 */
  inputs: Record<string, string>;
  retry: number;
  timeout_seconds: number;
}

/** DAG 边：依赖关系。 */
export interface WorkflowEdge {
  upstream: string;
  downstream: string;
}

/** 工作流规格（可序列化为 YAML 模板）。 */
export interface WorkflowSpec {
  name: string;
  version: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  /** 工作流级输入。 */
  inputs: Record<string, Artifact>;
  /** 形如 { "out": "${node_id.out}" }。 */
  outputs: Record<string, string>;
  metadata: Record<string, unknown>;
}

/** 工作流事件类型。 */
export type WorkflowEventType =
  | 'node_started'
  | 'node_completed'
  | 'node_failed'
  | 'node_skipped'
  | 'workflow_completed'
  | 'workflow_failed';

/** 工作流事件。 */
export interface WorkflowEvent {
  workflow_run_id: string;
  node_id?: string;
  event_type: WorkflowEventType;
  payload: TaskResult | TaskProgress;
  timestamp: number;
}

/** 工作流运行状态摘要。 */
export interface WorkflowRunStatus {
  workflow_run_id: string;
  spec_name: string;
  status: TaskStatus;
  started_at: number;
  completed_at?: number;
  node_statuses: Record<string, TaskStatus>;
  error?: string;
}

// 抽象接口（仅类型声明，供前端 store / composable 实现时参考）

/** 任务执行器接口（前端通过 HTTP 调用后端实现）。 */
export interface ITaskExecutor {
  submit(
    task_type: string,
    params: Record<string, unknown>,
    opts?: {
      owner_id?: string;
      idempotency_key?: string;
      priority?: TaskPriority;
      timeout_seconds?: number;
    },
  ): Promise<string>;
  get(job_id: string): Promise<TaskResult>;
  cancel(job_id: string): Promise<boolean>;
  subscribe(job_id: string): AsyncIterable<TaskProgress>;
}

/** 工作流执行器接口。 */
export interface IWorkflowRunner {
  run(
    spec: WorkflowSpec,
    opts?: {
      inputs?: Record<string, Artifact>;
      resume_from?: string;
      owner_id?: string;
    },
  ): Promise<string>;
  getStatus(workflow_run_id: string): Promise<WorkflowRunStatus>;
  cancel(workflow_run_id: string): Promise<boolean>;
  subscribe(workflow_run_id: string): AsyncIterable<WorkflowEvent>;
}

export const CONTRACTS_TASK_VERSION = '1.0.0';
