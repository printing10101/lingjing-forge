import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  classifyErrorByCode,
  classifySeverity,
  getStringErrorCode,
  buildErrorFromResponse,
  buildErrorFromAxiosError,
  buildErrorFromError,
  isNetworkError,
  shouldShowConflictDialog,
  toErrorBusPayload,
  collectDiagnosticContext,
  generateDiagnosticText,
  copyDiagnosticText,
  type StandardError,
} from '@/utils/error-handler'

describe('error-handler', () => {
  describe('classifyErrorByCode', () => {
    it('classifies business error codes (1xxx)', () => {
      expect(classifyErrorByCode(1001)).toBe('business')
      expect(classifyErrorByCode(1005)).toBe('business')
    })

    it('classifies validation error code', () => {
      expect(classifyErrorByCode(1002)).toBe('validation')
    })

    it('classifies auth error codes', () => {
      expect(classifyErrorByCode(1003)).toBe('auth')
      expect(classifyErrorByCode(1004)).toBe('auth')
    })

    it('classifies system error codes (2xxx)', () => {
      expect(classifyErrorByCode(2001)).toBe('system')
      expect(classifyErrorByCode(2002)).toBe('system')
    })

    it('classifies external service error codes (6xxx)', () => {
      expect(classifyErrorByCode(6001)).toBe('external')
      expect(classifyErrorByCode(6002)).toBe('external')
    })

    it('classifies manufacturing string codes (E1xxx-E4xxx)', () => {
      expect(classifyErrorByCode('E1001')).toBe('manufacturing')
      expect(classifyErrorByCode('E2001')).toBe('manufacturing')
      expect(classifyErrorByCode('E3004')).toBe('manufacturing')
      expect(classifyErrorByCode('E4001')).toBe('manufacturing')
    })

    it('classifies system string codes (E5xxx)', () => {
      expect(classifyErrorByCode('E5001')).toBe('system')
    })

    it('returns unknown for unrecognized codes', () => {
      expect(classifyErrorByCode(9999)).toBe('unknown')
      expect(classifyErrorByCode('E9001')).toBe('unknown')
    })
  })

  describe('classifySeverity', () => {
    it('classifies severity by HTTP status', () => {
      expect(classifySeverity(200)).toBe('info')
      expect(classifySeverity(400)).toBe('warning')
      expect(classifySeverity(500)).toBe('error')
    })

    it('classifies severity by error code', () => {
      expect(classifySeverity(undefined, 2001)).toBe('error') // system
      expect(classifySeverity(undefined, 6001)).toBe('error') // external
      expect(classifySeverity(undefined, 1001)).toBe('warning') // business
    })

    it('returns error as default', () => {
      expect(classifySeverity()).toBe('error')
    })
  })

  describe('getStringErrorCode', () => {
    it('converts known numeric codes to string', () => {
      expect(getStringErrorCode(1001)).toBe('BIZ_NOT_FOUND')
      expect(getStringErrorCode(2001)).toBe('SYS_INTERNAL')
      expect(getStringErrorCode(6001)).toBe('EXT_LLM_ERROR')
    })

    it('returns ERR_ prefix for unknown codes', () => {
      expect(getStringErrorCode(9999)).toBe('ERR_9999')
    })
  })

  describe('buildErrorFromResponse', () => {
    it('builds standard error from API response', () => {
      const response = {
        status: 404,
        data: {
          code: 1001,
          error_code: 'BIZ_NOT_FOUND',
          message: '资源未找到',
          severity: 'warning',
          timestamp: '2026-06-15T10:30:00.000Z',
          request_id: 'req-123',
          trace_id: 'trace-123',
        },
      }

      const error = buildErrorFromResponse(response)

      expect(error.code).toBe(1001)
      expect(error.errorCode).toBe('BIZ_NOT_FOUND')
      expect(error.message).toBe('资源未找到')
      expect(error.severity).toBe('warning')
      expect(error.requestId).toBe('req-123')
      expect(error.traceId).toBe('trace-123')
    })

    it('handles missing fields gracefully', () => {
      const response = { status: 500, data: {} }
      const error = buildErrorFromResponse(response)

      expect(error.code).toBe(500)
      expect(error.message).toBe('操作失败')
      expect(error.errorType).toBe('unknown')
    })
  })

  describe('buildErrorFromAxiosError', () => {
    it('builds error from network error', () => {
      const axiosError = {
        code: 'ERR_NETWORK',
        message: 'Network Error',
      }

      const error = buildErrorFromAxiosError(axiosError)

      expect(error.code).toBe(0)
      expect(error.errorCode).toBe('NETWORK_ERROR')
      expect(error.errorType).toBe('network')
      expect(error.message).toContain('网络')
    })

    it('builds error from HTTP error response', () => {
      const axiosError = {
        response: {
          status: 403,
          data: {
            code: 1004,
            message: '权限不足',
          },
        },
      }

      const error = buildErrorFromAxiosError(axiosError)

      expect(error.code).toBe(1004)
      expect(error.message).toBe('权限不足')
      expect(error.errorType).toBe('auth')
    })
  })

  describe('buildErrorFromError', () => {
    it('builds error from standard Error', () => {
      const err = new Error('测试错误')
      const error = buildErrorFromError(err, 2001, '系统错误')

      expect(error.code).toBe(2001)
      expect(error.message).toBe('系统错误')
      expect(error.originalError).toBe(err)
    })
  })

  describe('isNetworkError', () => {
    it('detects network errors', () => {
      expect(isNetworkError({ code: 'ERR_NETWORK' })).toBe(true)
      expect(isNetworkError({ code: 'ECONNABORTED' })).toBe(true)
      expect(isNetworkError({ message: 'Network Error' })).toBe(true)
      expect(isNetworkError({ message: 'timeout' })).toBe(true)
    })

    it('returns false for non-network errors', () => {
      expect(isNetworkError({ code: 'OTHER' })).toBe(false)
      expect(isNetworkError({})).toBe(false)
    })
  })

  describe('shouldShowConflictDialog', () => {
    it('returns true for conflict data', () => {
      const data = {
        severity: 'error',
        error_code: 'E1001',
        suggestion: '调整参数',
      }
      expect(shouldShowConflictDialog(data)).toBe(true)
    })

    it('returns false for incomplete data', () => {
      expect(shouldShowConflictDialog({})).toBe(false)
      expect(shouldShowConflictDialog({ severity: 'error' })).toBe(false)
    })
  })

  describe('toErrorBusPayload', () => {
    it('converts standard error to error bus payload', () => {
      const error: StandardError = {
        code: 1001,
        errorCode: 'BIZ_NOT_FOUND',
        message: '资源未找到',
        errorType: 'business',
        severity: 'warning',
        timestamp: '2026-06-15T10:30:00.000Z',
        requestId: 'req-123',
        traceId: 'trace-123',
        suggestion: '请检查参数',
        recoverable: true,
        adjustedValues: { param1: 100 },
      }

      const payload = toErrorBusPayload(error)

      expect(payload.title).toBe('资源未找到')
      expect(payload.code).toBe(1001)
      expect(payload.severity).toBe('warning')
      expect(payload.suggestion).toBe('请检查参数')
      expect(payload.recoverable).toBe(true)
      expect(payload.adjusted_values).toEqual({ param1: 100 })
    })
  })

  describe('collectDiagnosticContext', () => {
    it('collects diagnostic context', () => {
      const error: StandardError = {
        code: 2001,
        errorCode: 'SYS_INTERNAL',
        message: '系统错误',
        errorType: 'system',
        severity: 'error',
        timestamp: '2026-06-15T10:30:00.000Z',
        requestId: 'req-456',
        traceId: 'trace-456',
      }

      const context = collectDiagnosticContext(error, { extra: 'info' })

      expect(context.error).toBe(error)
      expect(context.userAgent).toBeDefined()
      expect(context.currentUrl).toBeDefined()
      expect(context.timestamp).toBeDefined()
      expect(context.extra).toEqual({ extra: 'info' })
    })
  })

  describe('generateDiagnosticText', () => {
    it('generates readable diagnostic text', () => {
      const error: StandardError = {
        code: 1001,
        errorCode: 'BIZ_NOT_FOUND',
        message: '资源未找到',
        errorType: 'business',
        severity: 'warning',
        timestamp: '2026-06-15T10:30:00.000Z',
        requestId: 'req-789',
        traceId: 'trace-789',
        path: '/api/v1/test',
        suggestion: '请检查ID',
      }

      const context = collectDiagnosticContext(error)
      const text = generateDiagnosticText(context)

      expect(text).toContain('=== 错误诊断信息 ===')
      expect(text).toContain('错误码: BIZ_NOT_FOUND')
      expect(text).toContain('消息: 资源未找到')
      expect(text).toContain('请求ID: req-789')
      expect(text).toContain('路径: /api/v1/test')
      expect(text).toContain('建议: 请检查ID')
      expect(text).toContain('===================')
    })
  })

  describe('copyDiagnosticText', () => {
    beforeEach(() => {
      // Mock clipboard API using vi.spyOn
      vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
    })

    afterEach(() => {
      vi.restoreAllMocks()
    })

    it('copies diagnostic text to clipboard', async () => {
      const error: StandardError = {
        code: 1001,
        errorCode: 'BIZ_NOT_FOUND',
        message: '测试',
        errorType: 'business',
        severity: 'warning',
        timestamp: '2026-06-15T10:30:00.000Z',
        requestId: 'req-test',
        traceId: 'trace-test',
      }

      const context = collectDiagnosticContext(error)
      const result = await copyDiagnosticText(context)

      expect(result).toBe(true)
      expect(navigator.clipboard.writeText).toHaveBeenCalled()
    })
  })
})
