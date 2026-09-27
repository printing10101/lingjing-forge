/**
 * 后端进程状态管理 Composable
 *
 * 通过 Tauri 提供的 IPC 与后端 Sidecar 管理器通信：
 * - get_backend_state: 获取当前快照
 * - start_backend / stop_backend / restart_backend: 主动控制
 * - ping_backend: 主动 HTTP 健康探测
 *
 * 同时订阅 'sidecar://state' 事件，接收后端状态变化推送。
 */

import { reactive, ref, onMounted, type Ref } from 'vue'
import { setBackendPort } from '@/utils/http'

export type BackendStatusKind =
  | 'idle'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'crashed'
  | 'failed'
  | 'stopped'

export interface BackendState {
  status: BackendStatusKind
  pid: number | null
  progress: number
  message: string
  last_error: string | null
  started_at: string | null
  restart_count: number
  port: number
}

const DEFAULT_STATE: BackendState = {
  status: 'idle',
  pid: null,
  progress: 0,
  message: '等待启动后端服务',
  last_error: null,
  started_at: null,
  restart_count: 0,
  port: 8765,
}

/**
 * 检测当前是否运行在 Tauri 容器内
 */
// Tauri 注入到 window 的内部 API 标记，无官方类型定义，使用结构化类型而非 any
interface TauriInternalsWindow extends Window {
  __TAURI_INTERNALS__?: unknown
}

export function isTauriEnv(): boolean {
  // 仅检测内部标记是否存在，不调用任何内部方法，无副作用
  return (
    typeof window !== 'undefined' &&
    Boolean((window as TauriInternalsWindow).__TAURI_INTERNALS__)
  )
}

/**
 * 异步导入 @tauri-apps/api/core invoke，避免在非 Tauri 环境下导入失败
 */
async function tauriInvoke<T = unknown>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const mod = await import('@tauri-apps/api/core')
  return mod.invoke<T>(cmd, args)
}

async function tauriListen<T = unknown>(
  event: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  const mod = await import('@tauri-apps/api/event')
  const un = await mod.listen<T>(event, (e) => handler(e.payload))
  return un
}

/**
 * useBackendStatus 返回值契约。
 *
 * 显式声明返回类型，避免调用方依赖推断结果，便于后续重构内部实现而不破坏 API。
 */
export interface UseBackendStatusReturn {
  state: BackendState
  loading: Ref<boolean>
  tauriMode: Ref<boolean>
  refresh: () => Promise<void>
  start: () => Promise<void>
  stop: () => Promise<void>
  restart: () => Promise<void>
  ping: () => Promise<boolean>
}

// 模块级单例状态
//
// 设计说明：后端 sidecar 状态是应用级全局状态，应在整个应用生命周期内共享，
// 而非随组件挂载/卸载反复创建销毁。因此采用模块级单例模式：
// - `_state`：跨组件共享的响应式状态，所有调用 useBackendStatus 的组件访问同一实例
// - `_unlisten`：Tauri 事件监听器卸载函数，应用生命周期内只注册一次
// - `_initialized`：确保监听器只被首个使用此 composable 的组件注册一次
//
// 不在 onBeforeUnmount 中清理监听器的原因：
//   后端状态推送需要持续接收，即使当前没有组件展示状态（例如后台保活场景）。
//   应用关闭时 Tauri 运行时会自动清理所有事件监听器，无需手动 dispose。
//
// HMR 注意：开发环境热更新时模块状态会被重置，旧监听器可能残留。
//   这仅影响开发环境，生产构建不会触发 HMR。
let _state: ReturnType<typeof reactive<BackendState>> | null = null
const _unlisten: Array<() => void> = []
let _initialized = false

/**
 * 统一的状态写入入口：合并后端快照并同步 axios baseURL。
 *
 * 端口冲突修复：Rust 端在 8765 被占用时会自动切换空闲端口并通过
 * state.port 下发。桌面模式下所有业务 API 依赖 http.ts 的 baseURL
 * 指向实际端口，因此每次状态更新都必须调用 setBackendPort 跟随。
 * （非 Tauri 环境不会走到这里——refresh/start 等在 isTauriEnv() 为
 * false 时直接 return，浏览器开发模式继续用 vite proxy 的相对路径。）
 */
function applyState(next: BackendState | null | undefined): void {
  if (!_state || !next) return
  Object.assign(_state, next)
  if (typeof next.port === 'number' && next.port > 0) {
    setBackendPort(next.port)
  }
}

export function useBackendStatus(): UseBackendStatusReturn {
  if (!_state) {
    _state = reactive<BackendState>({ ...DEFAULT_STATE })
  }

  const loading = ref(false)
  const tauriMode = ref(false)

  async function refresh() {
    if (!isTauriEnv()) {
      tauriMode.value = false
      return
    }
    tauriMode.value = true
    try {
      const next = await tauriInvoke<BackendState>('get_backend_state')
      applyState(next)
    } catch (e: unknown) {
      // 后台状态刷新失败不应阻塞 UI，记录便于排查；UI 层可通过 state.status 感知异常
      console.warn('[useBackendStatus] refresh failed:', e)
    }
  }

  async function start() {
    if (!isTauriEnv()) return
    loading.value = true
    try {
      const next = await tauriInvoke<BackendState>('start_backend')
      applyState(next)
    } catch (e) {
      if (_state) {
        _state.last_error = String(e)
        _state.status = 'failed'
      }
    } finally {
      loading.value = false
    }
  }

  async function stop() {
    if (!isTauriEnv()) return
    loading.value = true
    try {
      const next = await tauriInvoke<BackendState>('stop_backend')
      applyState(next)
    } catch (e: unknown) {
      // stop 由 UI 层主动触发，失败时记录并保留状态，由调用方决定是否提示用户
      console.warn('[useBackendStatus] stop failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function restart() {
    if (!isTauriEnv()) return
    loading.value = true
    try {
      const next = await tauriInvoke<BackendState>('restart_backend')
      applyState(next)
    } catch (e) {
      if (_state) {
        _state.last_error = String(e)
        _state.status = 'failed'
      }
    } finally {
      loading.value = false
    }
  }

  async function ping(): Promise<boolean> {
    if (!isTauriEnv()) return true
    try {
      return await tauriInvoke<boolean>('ping_backend')
    } catch {
      return false
    }
  }

  if (!_initialized) {
    _initialized = true
    onMounted(async () => {
      await refresh()
      try {
        const offState = await tauriListen<BackendState>('sidecar://state', (payload) => {
          applyState(payload)
        })
        const offTerm = await tauriListen<{ code: number | null; signal: number | null }>(
          'sidecar://terminated',
          (payload) => {
            if (!_state) return
            if (_state.status !== 'stopping' && _state.status !== 'stopped') {
              _state.status = 'crashed'
              _state.message = `后端进程异常退出 (code=${payload?.code ?? '?'})`
              _state.pid = null
            }
          },
        )
        const offError = await tauriListen<string>('sidecar://error', (msg) => {
          if (_state) _state.last_error = msg
        })
        _unlisten.push(offState, offTerm, offError)
      } catch (e: unknown) {
        // 监听器注册失败会导致后续状态丢失，必须记录以便排查
        console.error('[useBackendStatus] listener setup failed:', e)
      }
    })
  }

  return {
    state: _state!,
    loading,
    tauriMode,
    refresh,
    start,
    stop,
    restart,
    ping,
  }
}
