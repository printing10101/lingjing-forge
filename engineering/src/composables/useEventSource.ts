/**
 * Server-Sent Events (SSE) 事件源管理
 * 提供连接、重连、事件处理等完整功能
 *
 * 连接生命周期（epoch 竞态防护 / 指数退避重连）由
 * sseConnection.createSseConnection 统一提供，本文件只负责任务
 * 事件（queued/started/progress/...）的状态推导。
 */

import { ref, unref, type Ref } from "vue";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { resolveBackendUrl } from "@/utils/http";
import { createSseConnection } from "@/composables/sseConnection";

/** SSE事件负载数据结构 */
export interface SSEEventData {
  percent?: number;
  metrics?: Record<string, unknown>;
  error?: string;
  status?: string;
  [key: string]: unknown;
}

export interface SSEEvent {
  type:
    | "queued"
    | "started"
    | "progress"
    | "complete"
    | "failed"
    | "cancelled"
    | "done";
  data: SSEEventData;
  timestamp: Date;
}

export interface UseEventSourceOptions {
  autoReconnect?: boolean;
  maxRetries?: number;
  baseDelay?: number;
  maxDelay?: number;
}

export interface UseEventSourceReturn {
  events: ReturnType<typeof ref<SSEEvent[]>>;
  isConnected: ReturnType<typeof ref<boolean>>;
  isDone: ReturnType<typeof ref<boolean>>;
  currentStatus: ReturnType<typeof ref<string | null>>;
  progress: ReturnType<typeof ref<number>>;
  lastProgressData: ReturnType<typeof ref<Record<string, unknown> | null>>;
  error: ReturnType<typeof ref<string | null>>;
  connect: () => void;
  close: () => void;
  reset: () => void;
}

const JOB_EVENT_TYPES = [
  "queued",
  "started",
  "progress",
  "complete",
  "failed",
  "cancelled",
  "done",
] as const;

export function useEventSource(
  jobId: string | Ref<string>,
  options: UseEventSourceOptions = {},
): UseEventSourceReturn {
  const events = ref<SSEEvent[]>([]);
  const isDone = ref(false);
  const currentStatus = ref<string | null>(null);
  const progress = ref(0);
  const lastProgressData = ref<Record<string, unknown> | null>(null);
  const error = ref<string | null>(null);

  const connection = createSseConnection({
    tag: "[useEventSource]",
    eventTypes: JOB_EVENT_TYPES,
    autoReconnect: options.autoReconnect,
    maxRetries: options.maxRetries,
    baseDelay: options.baseDelay,
    maxDelay: options.maxDelay,
    isDone: () => isDone.value,
    // 修复（历史）： jobId 为 Ref 且值为空串时会构造 `/jobs//stream` 错误 URL
    // → 404 → 无限重连；connect 前先校验 unref 后的实际值。
    canConnect: () => Boolean(unref(jobId)),
    buildUrl: () => {
      // 桌面模式：EventSource 不走 axios baseURL，必须显式解析为后端实际端口的完整 URL
      return resolveBackendUrl(
        buildApiPath(API_CONFIG.JOBS, `/${unref(jobId)}/stream`),
      );
    },
    onEvent: (eventType, raw) => {
      const sseEvent: SSEEvent = {
        type: eventType as SSEEvent["type"],
        data: raw as SSEEventData,
        timestamp: new Date(),
      };
      events.value.push(sseEvent);
      handleEvent(sseEvent);
    },
  });

  const { connect, close } = connection;

  /**
   * 处理接收到的SSE事件
   * @param event - SSE事件对象
   */
  const handleEvent = (event: SSEEvent): void => {
    switch (event.type) {
      case "queued":
        currentStatus.value = "queued";
        progress.value = 0;
        break;

      case "started":
        currentStatus.value = "running";
        progress.value = 5;
        break;

      case "progress":
        currentStatus.value = "running";
        progress.value = event.data.percent ?? 0;
        lastProgressData.value = event.data.metrics ?? null;
        break;

      case "complete":
        currentStatus.value = "completed";
        progress.value = 100;
        isDone.value = true;
        close();
        break;

      case "failed":
        currentStatus.value = "failed";
        error.value = event.data.error ?? "Unknown error occurred";
        isDone.value = true;
        close();
        break;

      case "cancelled":
        currentStatus.value = "cancelled";
        isDone.value = true;
        close();
        break;

      case "done":
        currentStatus.value = event.data.status ?? currentStatus.value;
        isDone.value = true;
        close();
        break;
    }
  };

  /**
   * 重置所有状态到初始值
   */
  const reset = (): void => {
    // 递增 epoch 使 in-flight 事件不再写入旧 events 数组
    connection.invalidate();
    events.value = [];
    currentStatus.value = null;
    progress.value = 0;
    lastProgressData.value = null;
    error.value = null;
    isDone.value = false;
  };

  return {
    events,
    isConnected: connection.isConnected,
    isDone,
    currentStatus,
    progress,
    lastProgressData,
    error,
    connect,
    close,
    reset,
  };
}
