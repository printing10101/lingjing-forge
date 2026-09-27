/**
 * 前端统一错误处理模块
 * 
 * 提供：
 * - 统一的错误捕获和处理逻辑
 * - 错误分类和标准化
 * - 与后端结构化错误响应的对接
 * - 诊断信息收集支持
 */

import type { ErrorDialogPayload } from '@/composables/useErrorBus'

// 错误类型定义

/**
 * 错误分类
 */
export type ErrorType = 
  | 'business'      // 业务错误
  | 'system'        // 系统错误
  | 'external'      // 外部服务错误
  | 'validation'    // 参数校验错误
  | 'auth'          // 认证授权错误
  | 'network'       // 网络错误
  | 'manufacturing' // 制造工艺错误
  | 'unknown'       // 未知错误

/**
 * 错误严重程度
 */
export type ErrorSeverity = 'info' | 'warning' | 'error' | 'critical'

/**
 * 标准化错误对象
 */
export interface StandardError {
  /** 数值错误码 */
  code: number
  /** 字符串错误标识 */
  errorCode: string
  /** 用户可读错误消息 */
  message: string
  /** 错误分类 */
  errorType: ErrorType
  /** 严重程度 */
  severity: ErrorSeverity
  /** ISO 8601 格式时间戳 */
  timestamp: string
  /** 请求追踪ID */
  requestId: string
  /** 链路追踪ID */
  traceId: string
  /** 请求路径 */
  path?: string
  /** 详细错误信息 */
  detail?: unknown
  /** 修复建议 */
  suggestion?: string
  /** 是否可自动恢复 */
  recoverable?: boolean
  /** 自动调整后的参数值 */
  adjustedValues?: Record<string, unknown>
  /** 原始错误对象 */
  originalError?: unknown
}

/**
 * 诊断信息上下文
 */
export interface DiagnosticContext {
  /** 错误对象 */
  error: StandardError
  /** 用户代理 */
  userAgent: string
  /** 当前URL */
  currentUrl: string
  /** 屏幕分辨率 */
  screenResolution: string
  /** 浏览器语言 */
  language: string
  /** 时间戳 */
  timestamp: string
  /** 额外信息 */
  extra?: Record<string, unknown>
}

// 错误码映射

/**
 * 数值错误码到字符串标识的映射
 */
const NUMERIC_TO_STRING_CODE: Record<number, string> = {
  1001: 'BIZ_NOT_FOUND',
  1002: 'BIZ_VALIDATION',
  1003: 'AUTH_UNAUTHORIZED',
  1004: 'AUTH_FORBIDDEN',
  1005: 'BIZ_CONFLICT',
  1006: 'BIZ_BAD_REQUEST',
  1007: 'BIZ_RATE_LIMIT',
  2001: 'SYS_INTERNAL',
  2002: 'SYS_UNAVAILABLE',
  2003: 'SYS_GATEWAY',
  2004: 'SYS_TIMEOUT',
  3001: 'REPO_ERROR',
  3002: 'REPO_NOT_FOUND',
  3003: 'REPO_STORAGE',
  4001: 'LOCK_ERROR',
  4002: 'LOCK_CONFLICT',
  6001: 'EXT_LLM_ERROR',
  6002: 'EXT_LLM_RATE_LIMIT',
  6003: 'EXT_LLM_RESPONSE',
  7001: 'BIZ_CAD_ERROR',
  7002: 'BIZ_CAD_SCRIPT',
  7003: 'BIZ_CAD_EXPORT',
}

/**
 * 错误码范围到类型的映射
 */
const CODE_RANGES: Array<{ min: number; max: number; type: ErrorType }> = [
  { min: 1000, max: 1099, type: 'business' },
  { min: 2000, max: 2099, type: 'system' },
  { min: 3000, max: 3099, type: 'business' },
  { min: 4000, max: 4099, type: 'business' },
  { min: 5000, max: 5099, type: 'system' },
  { min: 6000, max: 6099, type: 'external' },
  { min: 7000, max: 7099, type: 'business' },
]

// 错误分类工具函数

/**
 * 根据错误码推断错误分类
 */
export function classifyErrorByCode(code: number | string): ErrorType {
  if (typeof code === 'string') {
    // 字符串错误码 (E1xxx-E5xxx)
    const prefix = code.substring(0, 2)
    if (['E1', 'E2', 'E3', 'E4'].includes(prefix)) {
      return 'manufacturing'
    }
    if (prefix === 'E5') {
      return 'system'
    }
    return 'unknown'
  }

  // 特殊处理校验和认证错误（优先于范围检查）
  if (code === 1002) return 'validation'
  if (code === 1003 || code === 1004) return 'auth'

  // 数值错误码范围检查
  for (const range of CODE_RANGES) {
    if (code >= range.min && code <= range.max) {
      return range.type
    }
  }

  return 'unknown'
}

/**
 * 根据HTTP状态码或错误码推断严重程度
 */
export function classifySeverity(
  httpStatus?: number,
  code?: number | string
): ErrorSeverity {
  if (httpStatus !== undefined) {
    if (httpStatus < 400) return 'info'
    if (httpStatus < 500) return 'warning'
    return 'error'
  }

  if (code !== undefined) {
    const errorType = classifyErrorByCode(code)
    if (errorType === 'system' || errorType === 'external') {
      return 'error'
    }
    return 'warning'
  }

  return 'error'
}

/**
 * 将数值错误码转换为字符串标识
 */
export function getStringErrorCode(numericCode: number): string {
  return NUMERIC_TO_STRING_CODE[numericCode] || `ERR_${numericCode}`
}

// 错误对象构建

/**
 * 从API响应构建标准化错误对象
 */
export function buildErrorFromResponse(
  response: { data?: Record<string, unknown>; status?: number },
  originalError?: unknown
): StandardError {
  const data: Record<string, unknown> = response?.data || {}
  const status = response?.status

  const code: number | string = (data.code || data.error_code || status || 2001) as number | string
  const detailObj = typeof data.detail === 'object' && data.detail !== null ? (data.detail as Record<string, unknown>) : undefined
  const message = String(data.message || detailObj?.message || '操作失败')
  const errorCode = typeof code === 'string'
    ? code
    : (data.error_code || getStringErrorCode(code as number))
  const errorType = classifyErrorByCode(code)
  const severity: ErrorSeverity = (data.severity as ErrorSeverity) || classifySeverity(status, code as number | string)
  const timestamp = data.timestamp || new Date().toISOString()
  const requestId = data.request_id || data.trace_id || ''
  const traceId = data.trace_id || requestId
  const path = data.path
  const detail = data.detail
  const suggestion = data.suggestion
  const recoverable = data.recoverable
  const adjustedValues = data.adjusted_values

  return {
    code: typeof code === 'string' ? parseInt(code) || 2001 : (code as number),
    errorCode: errorCode as string,
    message: message as string,
    errorType,
    severity,
    timestamp: String(timestamp),
    requestId: String(requestId),
    traceId: String(traceId),
    path: path as string | undefined,
    detail,
    suggestion: suggestion as string | undefined,
    recoverable: recoverable as boolean | undefined,
    adjustedValues: adjustedValues as Record<string, unknown> | undefined,
    originalError,
  }
}

/**
 * 从Axios错误构建标准化错误对象
 */
export function buildErrorFromAxiosError(error: unknown): StandardError {
  const err = error as Record<string, unknown>
  // 网络错误
  if (!err.response) {
    const isNetworkErr = isNetworkError(error)
    return {
      code: 0,
      errorCode: 'NETWORK_ERROR',
      message: isNetworkErr 
        ? '网络连接错误，请检查网络后重试' 
        : (err.message as string) || '未知网络错误',
      errorType: 'network',
      severity: 'error',
      timestamp: new Date().toISOString(),
      requestId: '',
      traceId: '',
      originalError: error,
    }
  }

  // HTTP错误响应
  return buildErrorFromResponse(err.response as { data?: Record<string, unknown>; status?: number }, error)
}

/**
 * 从普通Error对象构建标准化错误对象
 */
export function buildErrorFromError(
  error: Error,
  code: number = 2001,
  message?: string
): StandardError {
  return {
    code,
    errorCode: getStringErrorCode(code),
    message: message || error.message || '未知错误',
    errorType: classifyErrorByCode(code),
    severity: classifySeverity(undefined, code),
    timestamp: new Date().toISOString(),
    requestId: '',
    traceId: '',
    originalError: error,
  }
}

// 网络错误检测

/**
 * 判断是否为网络错误
 */
export function isNetworkError(error: unknown): boolean {
  if (!error) return false
  const err = error as Record<string, unknown>
  return Boolean(
    err.code === 'ERR_NETWORK' ||
    err.code === 'ECONNABORTED' ||
    err.message === 'Network Error' ||
    (typeof err.message === 'string' && err.message.includes('timeout')) ||
    (typeof err.message === 'string' && err.message.includes('Network Error'))
  )
}

/**
 * 判断是否应该显示冲突对话框
 */
export function shouldShowConflictDialog(data: unknown): boolean {
  const d = data as Record<string, unknown>
  return Boolean(
    d?.severity && d?.error_code && d?.suggestion
  )
}

// 错误转换为ErrorBus载荷

/**
 * 将标准化错误转换为ErrorBus载荷
 */
export function toErrorBusPayload(error: StandardError): ErrorDialogPayload {
  return {
    title: error.message,
    code: error.code,
    message: error.message,
    severity: error.severity,
    detail: typeof error.detail === 'string' 
      ? error.detail 
      : JSON.stringify(error.detail || ''),
    suggestion: error.suggestion || '',
    recoverable: error.recoverable || false,
    adjusted_values: error.adjustedValues,
    error_id: error.requestId,
  }
}

// 诊断信息收集

/**
 * 收集诊断信息上下文
 */
export function collectDiagnosticContext(
  error: StandardError,
  extra?: Record<string, unknown>
): DiagnosticContext {
  return {
    error,
    userAgent: navigator.userAgent,
    currentUrl: window.location.href,
    screenResolution: `${window.screen.width}x${window.screen.height}`,
    language: navigator.language,
    timestamp: new Date().toISOString(),
    extra,
  }
}

/**
 * 生成人类可读的诊断信息文本
 */
export function generateDiagnosticText(context: DiagnosticContext): string {
  const lines = [
    '=== 错误诊断信息 ===',
    `时间: ${context.timestamp}`,
    `错误码: ${context.error.errorCode}`,
    `消息: ${context.error.message}`,
    `严重程度: ${context.error.severity}`,
    `请求ID: ${context.error.requestId || 'N/A'}`,
    `链路ID: ${context.error.traceId || 'N/A'}`,
  ]

  if (context.error.path) {
    lines.push(`路径: ${context.error.path}`)
  }

  lines.push('')
  lines.push('--- 环境信息 ---')
  lines.push(`浏览器: ${context.userAgent}`)
  lines.push(`当前URL: ${context.currentUrl}`)
  lines.push(`屏幕分辨率: ${context.screenResolution}`)
  lines.push(`语言: ${context.language}`)

  if (context.error.detail) {
    lines.push('')
    lines.push('--- 详细信息 ---')
    if (typeof context.error.detail === 'object') {
      lines.push(JSON.stringify(context.error.detail, null, 2))
    } else {
      lines.push(String(context.error.detail))
    }
  }

  if (context.error.suggestion) {
    lines.push('')
    lines.push(`建议: ${context.error.suggestion}`)
  }

  if (context.extra && Object.keys(context.extra).length > 0) {
    lines.push('')
    lines.push('--- 附加信息 ---')
    lines.push(JSON.stringify(context.extra, null, 2))
  }

  lines.push('')
  lines.push('===================')

  return lines.join('\n')
}

/**
 * 复制诊断信息到剪贴板
 */
export async function copyDiagnosticText(
  context: DiagnosticContext
): Promise<boolean> {
  const text = generateDiagnosticText(context)
  
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    } else {
      // 降级方案：使用 textarea
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const success = document.execCommand('copy')
      document.body.removeChild(textarea)
      return success
    }
  } catch {
    return false
  }
}

// 全局错误处理

/**
 * 全局错误处理器类型
 */
export type GlobalErrorHandler = (error: StandardError) => void

/**
 * 全局错误处理器集合
 */
const globalErrorHandlers: Set<GlobalErrorHandler> = new Set()

/**
 * 注册全局错误处理器
 */
export function registerGlobalErrorHandler(handler: GlobalErrorHandler): () => void {
  globalErrorHandlers.add(handler)
  return () => {
    globalErrorHandlers.delete(handler)
  }
}

/**
 * 触发全局错误处理器
 */
export function triggerGlobalErrorHandlers(error: StandardError): void {
  for (const handler of globalErrorHandlers) {
    try {
      handler(error)
    } catch (e: unknown) {
      // 单个全局处理器异常不应中断其他处理器分发，但需记录便于排查
      console.warn('[error-handler] triggerGlobalErrorHandlers: handler execution failed:', e)
    }
  }
}

/**
 * 安装全局错误捕获
 * @returns 清理函数，用于移除全局错误监听器
 */
export function installGlobalErrorCapture(): () => void {
  // 捕获未处理的Promise拒绝
  const rejectionHandler = (event: PromiseRejectionEvent) => {
    const error = event.reason
    const standardError = error instanceof Error
      ? buildErrorFromError(error)
      : buildErrorFromAxiosError(error)
    
    triggerGlobalErrorHandlers(standardError)
  }

  // 捕获未处理的错误
  const errorHandler = (event: ErrorEvent) => {
    const error = event.error
    const standardError = error instanceof Error
      ? buildErrorFromError(error)
      : buildErrorFromError(new Error(String(error)))
    
    triggerGlobalErrorHandlers(standardError)
  }

  window.addEventListener('unhandledrejection', rejectionHandler)
  window.addEventListener('error', errorHandler)

  // 返回清理函数
  return () => {
    window.removeEventListener('unhandledrejection', rejectionHandler)
    window.removeEventListener('error', errorHandler)
  }
}

// 兼容性导出（替代 errorUtils.ts）

/**
 * 从API错误响应中提取用户友好的错误消息
 * 优先顺序: response.data.message > response.data.detail > error.message
 * @param error - axios错误对象或任意错误
 * @param fallback - 当无法提取时的回退消息
 * @returns 错误消息字符串
 */
export function extractErrorMessage(error: unknown, fallback = '操作失败'): string {
  if (!error) return fallback

  // Axios response errors
  const err = error as Record<string, unknown>
  const response = err.response as Record<string, unknown> | undefined
  const data = response?.data as Record<string, unknown> | undefined
  if (data) {
    if (typeof data.message === 'string') return data.message
    if (data.detail) {
      if (typeof data.detail === 'string') return data.detail
      const detail = data.detail as Record<string, unknown>
      if (typeof detail.message === 'string') return detail.message
    }
  }

  // Generic error
  if (typeof error === 'string') return error
  if (typeof err.message === 'string') return err.message

  return fallback
}

/**
 * 格式化网络错误消息（带HTTP状态码）
 */
export function formatNetworkError(error: unknown): string {
  const err = error as Record<string, unknown>
  const response = err.response as Record<string, unknown> | undefined
  const status = response?.status as number | undefined
  if (status) {
    return `请求失败 (${status}): ${extractErrorMessage(error)}`
  }
  if (isNetworkError(error)) {
    return '网络连接错误，请检查网络后重试'
  }
  return extractErrorMessage(error)
}
