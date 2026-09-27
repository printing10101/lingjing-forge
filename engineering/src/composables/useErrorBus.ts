import { onBeforeUnmount, readonly, ref, type Ref } from 'vue'

/**
 * 统一的错误总线 composable。
 *
 * 取代散落在 http.ts、ErrorNotification.vue 等组件中通过
 * `window.dispatchEvent('manufacturing-error', ...)` 的隐式约定，
 * 提供：
 *   - 类型安全的事件载荷（ErrorDialogPayload）
 *   - 同一组件多次挂载时的 listener 计数保护
 *   - 测试时可注入自定义实现
 *
 * 用法：
 *   // 派发端
 *   const { emit } = useErrorBus()
 *   emit({ title: '...', severity: 'error', ... })
 *
 *   // 订阅端
 *   const { latest } = useErrorBus()
 *   watch(latest, (payload) => showDialog(payload))
 */

export interface ErrorDialogPayload {
  title: string
  code: string | number
  message: string
  severity: 'info' | 'warning' | 'error' | 'critical'
  detail: string
  suggestion: string
  recoverable: boolean
  adjusted_values?: Record<string, unknown>
  /** 关联的服务端错误 ID，便于跨端排查 */
  error_id?: string
}

/**
 * 用户在错误通知上点击"接受调整"时携带的载荷。
 */
export interface ErrorAcceptedPayload {
  id: string
  adjusted_values: Record<string, unknown>
}

/**
 * 用户在错误通知上点击"手动修改"时携带的载荷。
 */
export interface ErrorManualEditPayload {
  id: string
  error_code?: string | number
}

interface ErrorBus {
  emit(payload: ErrorDialogPayload): void
  on(handler: (payload: ErrorDialogPayload) => void): () => void
  latest: Readonly<Ref<ErrorDialogPayload | null>>
  /**
   * 派发用户接受自动调整的事件。返回值标识此次派发是否成功送达至少一个订阅者。
   */
  emitAccepted(payload: ErrorAcceptedPayload): boolean
  /**
   * 派发用户选择手动修改的事件。返回值标识此次派发是否成功送达至少一个订阅者。
   */
  emitManualEdit(payload: ErrorManualEditPayload): boolean
  /**
   * 订阅用户接受自动调整事件。
   */
  onAccepted(handler: (payload: ErrorAcceptedPayload) => void): () => void
  /**
   * 订阅用户选择手动修改事件。
   */
  onManualEdit(handler: (payload: ErrorManualEditPayload) => void): () => void
}

const handlers = new Set<(p: ErrorDialogPayload) => void>()
const acceptedHandlers = new Set<(p: ErrorAcceptedPayload) => void>()
const manualHandlers = new Set<(p: ErrorManualEditPayload) => void>()
const latestRef = ref<ErrorDialogPayload | null>(null)

function _emit(payload: ErrorDialogPayload): void {
  latestRef.value = payload
  for (const h of handlers) {
    try {
      h(payload)
    } catch {
      // listener 自身抛错不应影响其他订阅者
    }
  }
}

function _on(handler: (p: ErrorDialogPayload) => void): () => void {
  handlers.add(handler)
  return () => {
    handlers.delete(handler)
  }
}

function _fanout<T>(set: Set<(p: T) => void>, payload: T): boolean {
  if (set.size === 0) {
    // 没有订阅者时返回 false，调用方可据此判断
    return false
  }
  for (const h of set) {
    try {
      h(payload)
    } catch (e: unknown) {
      // 单个处理器异常不应中断其他处理器分发，但需记录便于排查
      console.warn('[useErrorBus] handler execution failed:', e)
    }
  }
  return true
}

function _onTyped<T>(set: Set<(p: T) => void>, handler: (p: T) => void): () => void {
  set.add(handler)
  return () => {
    set.delete(handler)
  }
}

/**
 * 错误总线 composable。组件卸载时自动取消订阅。
 */
export function useErrorBus(): ErrorBus {
  // 注册一次性的当前组件 listener（在 setup 中调用）
  let teardown: (() => void) | null = null
  let acceptedTeardown: (() => void) | null = null
  let manualTeardown: (() => void) | null = null

  function clearAll() {
    if (teardown) {
      teardown()
      teardown = null
    }
    if (acceptedTeardown) {
      acceptedTeardown()
      acceptedTeardown = null
    }
    if (manualTeardown) {
      manualTeardown()
      manualTeardown = null
    }
  }

  onBeforeUnmount(clearAll)

  return {
    emit: _emit,
    on(handler) {
      // 如果当前组件已有 listener，先清理
      if (teardown) teardown()
      teardown = _on(handler)
      return teardown
    },
    latest: readonly(latestRef),
    emitAccepted(payload) {
      return _fanout(acceptedHandlers, payload)
    },
    emitManualEdit(payload) {
      return _fanout(manualHandlers, payload)
    },
    onAccepted(handler) {
      if (acceptedTeardown) acceptedTeardown()
      acceptedTeardown = _onTyped(acceptedHandlers, handler)
      return acceptedTeardown
    },
    onManualEdit(handler) {
      if (manualTeardown) manualTeardown()
      manualTeardown = _onTyped(manualHandlers, handler)
      return manualTeardown
    },
  }
}

/**
 * 兼容旧代码：通过 window CustomEvent 派发 ``manufacturing-error``。
 * 监听 ``manufacturing-error`` 的旧组件仍可工作；新代码应使用 useErrorBus。
 */
export function emitManufacturingError(payload: ErrorDialogPayload): void {
  _emit(payload)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('manufacturing-error', { detail: payload }),
    )
  }
}
