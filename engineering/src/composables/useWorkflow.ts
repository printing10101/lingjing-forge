/**
 * DAG 工作流编排 composable
 *
 * 对应后端 python/app/api/v1/workflows.py（ADR-005 阶段 1）。
 * 封装 /api/v1/workflows REST API + SSE 事件流订阅，
 * 并维护工作流运行状态、节点状态、事件日志的响应式视图。
 *
 * 设计参考：src/composables/useEventSource.ts（SSE 重连 + 事件分发）
 */

import { ref, onUnmounted, unref, type Ref } from "vue";
import http, { resolveBackendUrl } from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { createSseConnection } from "@/composables/sseConnection";
import type {
  Artifact,
  TaskStatus,
  WorkflowEvent,
  WorkflowEventType,
  WorkflowRunStatus,
  WorkflowSpec,
} from "@/contracts/task";

// 类型定义

/** 工作流运行记录（列表项，对应 WorkflowRun.to_dict()）。 */
export interface WorkflowRunRecord {
  id: string;
  name: string;
  version: string;
  spec: WorkflowSpec;
  status: TaskStatus;
  inputs: Record<string, Artifact> | null;
  outputs: Record<string, unknown> | null;
  owner_id: string | null;
  error: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
  started_at: string | null;
  completed_at: string | null;
}

/** 工作流列表查询参数。 */
export interface ListWorkflowParams {
  status?: string;
  owner_id?: string;
  limit?: number;
  offset?: number;
}

/** 工作流列表响应。 */
export interface ListWorkflowResponse {
  workflows: WorkflowRunRecord[];
  limit: number;
  offset: number;
}

/** 提交/续跑请求体。 */
export interface RunRequestBody {
  spec: WorkflowSpec;
  inputs?: Record<string, Artifact>;
  owner_id?: string;
}

/** 校验结果。 */
export interface ValidateResult {
  valid: boolean;
  node_count: number;
  edge_count: number;
}

/** SSE 订阅选项。 */
export interface UseWorkflowStreamOptions {
  autoReconnect?: boolean;
  maxRetries?: number;
  baseDelay?: number;
  maxDelay?: number;
}

/** SSE 订阅状态。 */
export interface WorkflowStreamState {
  events: Ref<WorkflowEvent[]>;
  isConnected: Ref<boolean>;
  isDone: Ref<boolean>;
  currentStatus: Ref<TaskStatus | null>;
  nodeStatuses: Ref<Record<string, TaskStatus>>;
  error: Ref<string | null>;
  connect: () => void;
  close: () => void;
  reset: () => void;
}

// REST API 调用（无状态函数）

/** 后端统一响应壳：{ code, message, data, request_id }，code !== 0 由 http 拦截器抛错。 */
interface ApiEnvelope<T> {
  data: T;
  message?: string;
}

const BASE = API_CONFIG.WORKFLOWS;

/**
 * 校验工作流 Spec（不执行）。
 * @returns valid=true 表示通过；否则抛错（由 http 拦截器统一报错）
 */
export async function validateWorkflow(
  spec: WorkflowSpec,
): Promise<ValidateResult> {
  const res = await http.post<ApiEnvelope<ValidateResult>>(
    buildApiPath(BASE, "/validate"),
    spec,
  );
  return res.data.data;
}

/**
 * 提交工作流运行。
 * @returns 新建的 workflow_run_id
 */
export async function runWorkflow(
  body: RunRequestBody,
): Promise<{ workflow_run_id: string; status: string }> {
  const res = await http.post<
    ApiEnvelope<{ workflow_run_id: string; status: string }>
  >(buildApiPath(BASE, "/run"), body);
  return res.data.data;
}

/**
 * 断点续跑：从指定 workflow_run_id 继续，仅重跑 FAILED/PENDING 节点。
 * @returns 新建的 workflow_run_id（与原 run 不同）
 */
export async function resumeWorkflow(
  workflowRunId: string,
  body: RunRequestBody,
): Promise<{ workflow_run_id: string; status: string }> {
  const res = await http.post<
    ApiEnvelope<{ workflow_run_id: string; status: string }>
  >(buildApiPath(BASE, `/${workflowRunId}/resume`), body);
  return res.data.data;
}

/**
 * 获取工作流运行状态（含各节点状态）。
 */
export async function getWorkflowStatus(
  workflowRunId: string,
): Promise<WorkflowRunStatus> {
  const res = await http.get<ApiEnvelope<WorkflowRunStatus>>(
    buildApiPath(BASE, `/${workflowRunId}`),
  );
  return res.data.data;
}

/**
 * 取消工作流。下游未启动节点会被标记为 SKIPPED。
 */
export async function cancelWorkflow(
  workflowRunId: string,
): Promise<{ workflow_run_id: string; status: string }> {
  const res = await http.post<
    ApiEnvelope<{ workflow_run_id: string; status: string }>
  >(buildApiPath(BASE, `/${workflowRunId}/cancel`));
  return res.data.data;
}

/**
 * 列出工作流运行记录。
 */
export async function listWorkflows(
  params: ListWorkflowParams = {},
): Promise<ListWorkflowResponse> {
  const res = await http.get<ApiEnvelope<ListWorkflowResponse>>(
    buildApiPath(BASE, ""),
    { params },
  );
  return res.data.data;
}

/**
 * 删除工作流运行记录（含节点状态）。
 */
export async function deleteWorkflow(
  workflowRunId: string,
): Promise<{ workflow_run_id: string; deleted: boolean }> {
  const res = await http.delete<
    ApiEnvelope<{ workflow_run_id: string; deleted: boolean }>
  >(buildApiPath(BASE, `/${workflowRunId}`));
  return res.data.data;
}

// SSE 订阅（基于 EventSource，参考 useEventSource.ts）

/** 工作流事件类型全集（与后端 _serialize_event 对齐 + stream_error 本地兜底）。 */
const WORKFLOW_EVENT_TYPES: readonly WorkflowEventType[] = [
  "node_started",
  "node_completed",
  "node_failed",
  "node_skipped",
  "workflow_completed",
  "workflow_failed",
] as const;

/** 终态事件（触发后自动关闭连接）。 */
const TERMINAL_EVENTS: ReadonlySet<WorkflowEventType> =
  new Set<WorkflowEventType>(["workflow_completed", "workflow_failed"]);

export { TERMINAL_EVENTS };

/**
 * 订阅工作流事件流。
 *
 * EventSource 生命周期（epoch 竞态防护 / 指数退避重连）复用
 * sseConnection.ts 通用核心；本函数只负责工作流事件语义
 * （nodeStatuses 推导 / 终态收尾 / stream_error 兜底）。
 *
 * @param workflowRunId - 工作流运行 ID（字符串或 Ref，便于父组件切换 run 时自动重连）
 * @param options - SSE 重连参数
 */
export function useWorkflowStream(
  workflowRunId: string | Ref<string>,
  options: UseWorkflowStreamOptions = {},
): WorkflowStreamState {
  const events = ref<WorkflowEvent[]>([]);
  const isDone = ref(false);
  const currentStatus = ref<TaskStatus | null>(null);
  const nodeStatuses = ref<Record<string, TaskStatus>>({});
  const error = ref<string | null>(null);

  const handleEvent = (event: WorkflowEvent): void => {
    // 节点级事件：更新 nodeStatuses
    if (event.node_id) {
      switch (event.event_type) {
        case "node_started":
          nodeStatuses.value[event.node_id] = "running";
          break;
        case "node_completed":
          nodeStatuses.value[event.node_id] = "completed";
          break;
        case "node_failed":
          nodeStatuses.value[event.node_id] = "failed";
          break;
        case "node_skipped":
          nodeStatuses.value[event.node_id] = "skipped";
          break;
      }
    }

    // 工作流级事件：更新 currentStatus
    switch (event.event_type) {
      case "node_started":
      case "node_completed":
        currentStatus.value = "running";
        break;
      case "node_failed":
        // 单节点失败不立即置 workflow 为 failed，由 workflow_failed 事件统一收尾
        currentStatus.value = "running";
        break;
      case "workflow_completed":
        currentStatus.value = "completed";
        isDone.value = true;
        stream.close();
        break;
      case "workflow_failed": {
        currentStatus.value = "failed";
        // payload 为 TaskResult，error 字段非空时优先采用
        const failedPayload = event.payload as { error?: string };
        error.value = failedPayload?.error ?? "工作流执行失败";
        isDone.value = true;
        stream.close();
        break;
      }
    }
  };

  const stream = createSseConnection({
    tag: "[useWorkflowStream]",
    eventTypes: WORKFLOW_EVENT_TYPES,
    autoReconnect: options.autoReconnect,
    maxRetries: options.maxRetries,
    baseDelay: options.baseDelay,
    maxDelay: options.maxDelay,
    isDone: () => isDone.value,
    canConnect: () => Boolean(unref(workflowRunId)),
    buildUrl: () => {
      const id = unref(workflowRunId);
      // 桌面模式：EventSource 不走 axios baseURL，必须显式解析为后端实际端口的完整 URL
      return resolveBackendUrl(buildApiPath(BASE, `/${id}/stream`));
    },
    onError: (exhausted) => {
      if (!exhausted || isDone.value) return;
      // 重连耗尽仍未完成：标记错误状态，避免 UI 永久等待
      error.value = "工作流事件流连接失败，已达最大重试次数";
    },
    onEvent: (_eventType, raw) => {
      const payload = raw as WorkflowEvent;
      events.value.push(payload);
      handleEvent(payload);
    },
    // 后端在异常时主动推送 stream_error 事件（workflows.py line 331）
    extraListeners: (source, isCurrent) => {
      source.addEventListener("stream_error", (e: MessageEvent) => {
        if (!isCurrent()) return;
        try {
          const data = JSON.parse(e.data) as { error?: string };
          error.value = data.error ?? "工作流事件流异常";
          isDone.value = true;
        } catch {
          error.value = "工作流事件流异常（解析失败）";
          isDone.value = true;
        } finally {
          stream.close();
        }
      });
    },
  });

  const { connect, close } = stream;

  const reset = (): void => {
    // 递增 epoch 使 in-flight 事件不再写入旧 events 数组
    stream.invalidate();
    events.value = [];
    currentStatus.value = null;
    nodeStatuses.value = {};
    error.value = null;
    isDone.value = false;
  };

  return {
    events,
    isConnected: stream.isConnected,
    isDone,
    currentStatus,
    nodeStatuses,
    error,
    connect,
    close,
    reset,
  };
}

// 工作流管理 composable（聚合列表 + 提交 + 当前运行状态）

/**
 * 工作流管理 composable。
 *
 * 维护：
 *   - 工作流运行列表（分页 + 筛选）
 *   - 当前活跃工作流（用于详情/可视化）
 *   - SSE 事件流状态
 */
export function useWorkflow() {
  const workflows = ref<WorkflowRunRecord[]>([]);
  const loading = ref(false);
  const totalCount = ref(0);
  const currentPage = ref(1);
  const pageSize = ref(20);
  const statusFilter = ref<string>("");
  const ownerFilter = ref<string>("");

  const currentRunId = ref<string>("");
  const currentStatus = ref<WorkflowRunStatus | null>(null);
  const currentLoading = ref(false);
  // 修复竞态：refreshCurrentStatus 是 async，若用户在 A 的 HTTP 还在飞行时
  // 又切到 B（selectWorkflow(B)），A 的 await 完成后会用 A 的状态覆盖
  // currentStatus.value，导致 UI 短暂显示错误状态。每次发起 refresh 时
  // 递增 statusEpoch，await 完成后检查 epoch 是否仍匹配，不匹配则丢弃结果。
  let statusEpoch = 0;

  // SSE 订阅：run_id 切换时由 useWorkflowStream 内部重新建立连接
  const stream = useWorkflowStream(currentRunId, {
    autoReconnect: true,
    maxRetries: 10,
  });

  /**
   * 加载工作流列表。
   */
  async function loadWorkflows(): Promise<void> {
    loading.value = true;
    try {
      const params: ListWorkflowParams = {
        limit: pageSize.value,
        offset: (currentPage.value - 1) * pageSize.value,
      };
      if (statusFilter.value) params.status = statusFilter.value;
      if (ownerFilter.value) params.owner_id = ownerFilter.value;

      const res = await listWorkflows(params);
      workflows.value = res.workflows;
      // 后端目前不返回 total 字段；以返回数量 + offset 估算是否有下一页
      totalCount.value = res.workflows.length + res.offset;
    } catch (e: unknown) {
      console.warn("[useWorkflow] loadWorkflows failed:", e);
    } finally {
      loading.value = false;
    }
  }

  /**
   * 提交工作流运行并自动订阅 SSE。
   * @returns workflow_run_id
   */
  async function submitWorkflow(body: RunRequestBody): Promise<string> {
    const { workflow_run_id } = await runWorkflow(body);
    // 切换当前 run 并重置流状态后建立 SSE 连接
    stream.reset();
    currentRunId.value = workflow_run_id;
    stream.connect();
    // 立即拉取一次状态，避免 SSE 首事件到达前的空窗
    void refreshCurrentStatus();
    return workflow_run_id;
  }

  /**
   * 断点续跑。返回新 run_id 后自动切换订阅。
   */
  async function resumeCurrentWorkflow(
    originalRunId: string,
    body: RunRequestBody,
  ): Promise<string> {
    const { workflow_run_id } = await resumeWorkflow(originalRunId, body);
    stream.reset();
    currentRunId.value = workflow_run_id;
    stream.connect();
    void refreshCurrentStatus();
    return workflow_run_id;
  }

  /**
   * 取消当前工作流。
   */
  async function cancelCurrent(): Promise<void> {
    if (!currentRunId.value) return;
    await cancelWorkflow(currentRunId.value);
    void refreshCurrentStatus();
  }

  /**
   * 删除工作流运行记录。
   */
  async function removeWorkflow(workflowRunId: string): Promise<void> {
    await deleteWorkflow(workflowRunId);
    // 若删除的是当前正在订阅的 run，关闭 SSE
    if (currentRunId.value === workflowRunId) {
      stream.close();
      currentRunId.value = "";
      currentStatus.value = null;
    }
    // 从本地列表中移除
    workflows.value = workflows.value.filter((w) => w.id !== workflowRunId);
  }

  /**
   * 刷新当前工作流状态。
   */
  async function refreshCurrentStatus(): Promise<void> {
    if (!currentRunId.value) return;
    // 递增 epoch 使前一个 in-flight refresh 的结果失效
    statusEpoch += 1;
    const currentEpoch = statusEpoch;
    const targetRunId = currentRunId.value;
    currentLoading.value = true;
    try {
      const status = await getWorkflowStatus(targetRunId);
      // 若期间 currentRunId 已被切换（selectWorkflow/submitWorkflow），丢弃结果
      if (currentEpoch !== statusEpoch || currentRunId.value !== targetRunId)
        return;
      currentStatus.value = status;
    } catch (e: unknown) {
      if (currentEpoch !== statusEpoch) return;
      console.warn("[useWorkflow] refreshCurrentStatus failed:", e);
    } finally {
      if (currentEpoch === statusEpoch) {
        currentLoading.value = false;
      }
    }
  }

  /**
   * 选择某个工作流作为当前查看对象（自动建立 SSE 订阅）。
   */
  async function selectWorkflow(workflowRunId: string): Promise<void> {
    stream.reset();
    currentRunId.value = workflowRunId;
    await refreshCurrentStatus();
    // 仅在未到达终态时建立 SSE
    const st = currentStatus.value?.status;
    const isTerminal =
      st === "completed" || st === "failed" || st === "cancelled";
    if (!isTerminal) {
      stream.connect();
    }
  }

  onUnmounted(() => {
    stream.close();
  });

  return {
    // 列表
    workflows,
    loading,
    totalCount,
    currentPage,
    pageSize,
    statusFilter,
    ownerFilter,
    loadWorkflows,
    removeWorkflow,
    // 当前运行
    currentRunId,
    currentStatus,
    currentLoading,
    submitWorkflow,
    resumeCurrentWorkflow,
    cancelCurrent,
    refreshCurrentStatus,
    selectWorkflow,
    // SSE 流
    stream,
    // 校验（直接暴露，无需状态）
    validate: validateWorkflow,
  };
}
