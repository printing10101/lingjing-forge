/**
 * SSE 连接通用核心（epoch 竞态防护 + 指数退避重连）
 *
 * useEventSource（任务事件流）与 useWorkflowStream（工作流事件流）
 * 共享的 EventSource 生命周期管理。
 *
 * 竞态防护：每次 connect/close/invalidate 递增 streamEpoch，异步事件
 * 回调与重连定时器执行时检查 epoch 是否仍为最新，否则丢弃事件 /
 * 取消重连，避免：
 *   1. 快速切换 ID（A → B）：A 的 in-flight 事件仍被写入 B 的状态
 *   2. close → onerror 触发重连，定时器到期后建立幽灵连接
 */

import { onUnmounted, ref, type Ref } from "vue";

export interface CreateSseConnectionOptions {
  /** 构造 EventSource URL（桌面模式需显式解析为后端完整地址） */
  buildUrl: () => string;
  /** 需要监听的 SSE 事件类型（event: 字段） */
  eventTypes: readonly string[];
  /** 连接前置校验（如 id 非空）；返回 false 时 connect 直接返回 */
  canConnect?: () => boolean;
  autoReconnect?: boolean;
  maxRetries?: number;
  baseDelay?: number;
  maxDelay?: number;
  /** 返回 true 表示业务已到终态，收到错误时不再自动重连 */
  isDone?: () => boolean;
  /** 连接建立（epoch 校验通过后） */
  onOpen?: () => void;
  /**
   * 连接错误（epoch 校验通过后）。exhausted=true 表示自动重连不可用
   * （已耗尽重试次数 / 业务已终态 / 关闭了自动重连）
   */
  onError?: (exhausted: boolean) => void;
  /** 收到事件（已通过 epoch 校验，data 为 JSON.parse 结果） */
  onEvent: (eventType: string, data: unknown) => void;
  /** 解析失败 / 空连接告警前缀（如 '[useEventSource]'） */
  tag: string;
  /**
   * 额外事件监听（如工作流的 stream_error）。
   * isCurrent() 为 epoch 校验函数，回调内必须先调用它。
   */
  extraListeners?: (source: EventSource, isCurrent: () => boolean) => void;
}

export interface SseConnection {
  isConnected: Ref<boolean>;
  connect: () => void;
  /** 关闭连接并使所有 in-flight 回调失效 */
  close: () => void;
  /** 仅递增 epoch 并清零重试计数（不清状态，供业务 reset 使用） */
  invalidate: () => void;
}

export function createSseConnection(
  options: CreateSseConnectionOptions,
): SseConnection {
  const {
    buildUrl,
    eventTypes,
    canConnect,
    autoReconnect = true,
    maxRetries = 10,
    baseDelay = 1000,
    maxDelay = 30000,
    isDone,
    onOpen,
    onError,
    onEvent,
    tag,
    extraListeners,
  } = options;

  const isConnected = ref(false);

  let eventSource: EventSource | null = null;
  let retryCount = 0;
  let retryTimer: number | null = null;
  let streamEpoch = 0;

  const scheduleReconnect = (epoch: number): void => {
    retryCount++;
    const delay = Math.min(baseDelay * Math.pow(2, retryCount - 1), maxDelay);
    retryTimer = window.setTimeout(() => {
      // 重连到期时若 epoch 已不匹配（已被新 connect / close 取代），放弃重连
      if (epoch !== streamEpoch) return;
      connect();
    }, delay);
  };

  const connect = (): void => {
    if (canConnect && !canConnect()) return;
    if (eventSource) close();

    // 递增 epoch 使前一个连接的 in-flight 事件回调失效
    streamEpoch += 1;
    const currentEpoch = streamEpoch;

    eventSource = new EventSource(buildUrl());

    eventSource.onopen = (): void => {
      if (currentEpoch !== streamEpoch) return;
      isConnected.value = true;
      retryCount = 0;
      onOpen?.();
    };

    eventSource.onerror = (): void => {
      if (currentEpoch !== streamEpoch) return;
      isConnected.value = false;

      const canRetry =
        autoReconnect && !(isDone?.() ?? false) && retryCount < maxRetries;
      if (canRetry) {
        scheduleReconnect(currentEpoch);
      }
      onError?.(!canRetry);
    };

    const source = eventSource;
    if (!source) {
      // eventSource 未初始化（理论上不会发生），记录后跳过避免运行时崩溃
      console.warn(`${tag} eventSource is null when registering listeners`);
      return;
    }
    eventTypes.forEach((eventType) => {
      source.addEventListener(eventType, (event: MessageEvent) => {
        // 事件到达时若 epoch 已不匹配（已被新 connect / close 取代），直接丢弃
        if (currentEpoch !== streamEpoch) return;
        try {
          onEvent(eventType, JSON.parse(event.data));
        } catch (e: unknown) {
          // SSE 事件解析失败通常为协议异常或非预期数据，记录便于排查但不应断开连接
          console.warn(`${tag} event parse failed for`, eventType, e);
        }
      });
    });

    extraListeners?.(source, () => currentEpoch === streamEpoch);
  };

  const close = (): void => {
    // 递增 epoch 使任何 in-flight 事件回调立即失效
    streamEpoch += 1;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    isConnected.value = false;
  };

  const invalidate = (): void => {
    streamEpoch += 1;
    retryCount = 0;
  };

  onUnmounted(() => {
    close();
  });

  return { isConnected, connect, close, invalidate };
}
