import axios, { type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import {
  emitManufacturingError,
  type ErrorDialogPayload,
} from '@/composables/useErrorBus'
import { isNetworkError, shouldShowConflictDialog } from '@/utils/error-handler'

export type { ErrorDialogPayload }

/** 应用初始化阶段标记：首页加载后端健康检查完成前，抑制重复错误弹窗 */
let appInitializing = true
/** 用于外部设置初始化完成 */
export function setHttpReady() { appInitializing = false }

// 请求取消支持（AbortController 封装）

/**
 * 创建可取消的请求信号。
 *
 * 用法：
 * ```ts
 * const { signal, cancel } = createCancelToken()
 * http.get('/api/data', { signal })
 * // 页面卸载时：
 * cancel('组件卸载，取消请求')
 * ```
 */
export function createCancelToken() {
  const controller = new AbortController()
  return {
    signal: controller.signal,
    cancel: (reason?: string) => controller.abort(reason),
  }
}

// GET 请求自动重试（指数退避）

/** 可重试的 HTTP 状态码 */
const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504])

/** 最大重试次数 */
const MAX_RETRIES = 2

/** 重试基础延迟（毫秒），实际延迟 = base * 2^attempt */
const RETRY_BASE_DELAY_MS = 500

/**
 * 判断错误是否可重试：网络错误或特定 5xx 状态码。
 */
function isRetryableError(error: unknown): boolean {
  if (isNetworkError(error)) return true
  const status = (error as { response?: { status?: number } })?.response?.status
  return typeof status === 'number' && RETRYABLE_STATUS.has(status)
}

/**
 * 计算重试延迟（指数退避 + 抖动）。
 */
function computeRetryDelay(attempt: number): number {
  const jitter = Math.random() * 200
  return RETRY_BASE_DELAY_MS * Math.pow(2, attempt) + jitter
}

// 扩展 AxiosRequestConfig 以支持自定义重试标记
declare module 'axios' {
  interface AxiosRequestConfig {
    /** 跳过自动重试（即使满足重试条件也不重试） */
    _skipRetry?: boolean
    /** 已重试次数（内部使用） */
    _retryCount?: number
  }
}

/** Axios 请求超时时间（毫秒） */
const DEFAULT_TIMEOUT_MS = 30_000

// Axios 实例

const http = axios.create({
  timeout: DEFAULT_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
})

// Tauri 桌面模式：运行时动态 baseURL（端口冲突修复）
//
// 背景：本项目所有业务 API 均使用相对路径（如 /api/v1/...）。
// - 浏览器开发模式：vite dev server 的 proxy 把 /api 转发到后端，相对路径可用。
// - Tauri 生产模式：前端由 tauri://localhost 协议提供，没有任何 proxy，
//   相对路径请求会发到 tauri://localhost/api/... 而非后端，必须显式设置
//   baseURL 指向 Python sidecar 的实际监听地址 http://127.0.0.1:{port}。
// - 端口不能写死：Rust 端 sidecar.rs 在 8765 被占用（如 Docker 映射）时会
//   自动切换到其他空闲端口，前端必须跟随 state.port 动态更新 baseURL。
//
// 调用方：useBackendStatus.ts 在每次收到后端状态（get_backend_state 快照或
// sidecar://state 事件推送）时调用本函数同步端口。

/** 当前生效的后端端口（0 表示尚未设置，沿用相对路径） */
let currentBackendPort = 0

/**
 * 设置桌面模式下 axios 的 baseURL 指向后端实际端口。
 * 幂等：端口未变化时不重复赋值。
 */
export function setBackendPort(port: number): void {
  if (!port || port === currentBackendPort) return
  currentBackendPort = port
  http.defaults.baseURL = `http://127.0.0.1:${port}`
  console.info(`[http] baseURL 已切换为 http://127.0.0.1:${port}`)
}

/** 获取当前生效的后端端口（0 表示未设置） */
export function getBackendPort(): number {
  return currentBackendPort
}

/**
 * 将相对 API 路径解析为可直接访问后端的完整 URL。
 *
 * 供不走 axios 的请求使用（如 EventSource/SSE、原生 fetch）：
 * - 桌面模式（已通过 setBackendPort 设置端口）：返回 http://127.0.0.1:{port}{path}
 * - 浏览器开发模式（端口未设置）：原样返回相对路径，由 vite proxy 转发
 */
export function resolveBackendUrl(path: string): string {
  if (!currentBackendPort) return path
  const clean = path.startsWith('/') ? path : `/${path}`
  return `http://127.0.0.1:${currentBackendPort}${clean}`
}

// 请求拦截器：自动携带 Authorization header
http.interceptors.request.use(
  (config) => {
    // 安全修复：从 sessionStorage 读取 token（与 auth store 保持一致），
    // 不再使用 localStorage，降低 XSS 凭证窃取风险。关闭标签页后自动失效。
    const stored = sessionStorage.getItem('auth_token')
    const token = stored || ''
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

// 响应拦截器：业务错误码处理 + GET 自动重试
http.interceptors.response.use(
  (response) => {
    const data = response.data
    if (data && typeof data.code === 'number' && data.code !== 0) {
      if (shouldShowConflictDialog(data)) {
        const payload: ErrorDialogPayload = {
          title: data.message || '加工参数冲突',
          code: data.error_code || data.code,
          message: data.message || '操作异常',
          severity: data.severity || 'error',
          detail: data.detail || '',
          suggestion: data.suggestion || '',
          recoverable: data.recoverable || false,
          adjusted_values: data.adjusted_values,
        }
        emitManufacturingError(payload)
      } else {
        ElMessage.error(data.message || '操作失败')
      }
      return Promise.reject(new Error(data.message || '操作失败'))
    }
    return response
  },
  async (error) => {
// GET 自动重试
    const config = error.config as InternalAxiosRequestConfig | undefined
    if (
      config &&
      !config._skipRetry &&
      config.method?.toLowerCase() === 'get' &&
      isRetryableError(error)
    ) {
      const retryCount = config._retryCount ?? 0
      if (retryCount < MAX_RETRIES) {
        config._retryCount = retryCount + 1
        const delay = computeRetryDelay(retryCount)
        await new Promise((resolve) => setTimeout(resolve, delay))
        return http.request(config)
      }
    }

// 错误处理（原有逻辑）

    // 初始化阶段静默处理网络/服务器错误，避免批量弹出
    if (appInitializing && (isNetworkError(error) || !error.response || error.response?.status >= 500)) {
      return Promise.reject(error)
    }

    if (isNetworkError(error)) {
      // 重试耗尽后才提示
      const retryCount = config?._retryCount ?? 0
      if (retryCount >= MAX_RETRIES) {
        ElMessage.error(`网络连接错误，已重试 ${retryCount} 次仍失败，请检查网络状态`)
      } else {
        ElMessage.error('网络连接错误，请检查网络状态后重试')
      }
      return Promise.reject(error)
    }

    const response = error.response

    if (response) {
      const status = response.status
      const data = response.data || {}

      if (status === 401) {
        // 桌面应用：清除过期 token 和用户信息，触发重新登录
        // 不弹错误提示，保持桌面应用体验
        sessionStorage.removeItem('auth_token')
        sessionStorage.removeItem('auth_user')
        // 动态导入 auth store 避免循环依赖（http.ts 被 auth.ts 静态导入），
        // 同步更新 auth store 的响应式状态，使 isAuthenticated 立即变为 false
        import('@/stores/auth').then(({ useAuthStore }) => {
          useAuthStore().logout()
        })
        return Promise.reject(error)
      }

      if (status === 500) {
        ElMessage.error('系统内部错误，请联系管理员')
        return Promise.reject(error)
      }

      if (shouldShowConflictDialog(data)) {
        const payload: ErrorDialogPayload = {
          title: data.message || '加工参数冲突',
          code: data.error_code || data.code,
          message: data.message || '操作异常',
          severity: data.severity || 'error',
          detail: data.detail || '',
          suggestion: data.suggestion || '',
          recoverable: data.recoverable || false,
          adjusted_values: data.adjusted_values,
        }
        emitManufacturingError(payload)
        return Promise.reject(error)
      }

      const msg = data.message || `请求失败 (${status})`
      ElMessage.error(msg)
    } else {
      ElMessage.error('网络连接错误，请检查网络状态后重试')
    }
    return Promise.reject(error)
  },
)

export default http
