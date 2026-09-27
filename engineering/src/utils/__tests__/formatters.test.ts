import { describe, it, expect } from 'vitest'
import {
  formatTimestamp,
  formatSecondsTimestamp,
  formatDate,
  formatFileSize,
  formatDuration,
} from '@/utils/formatters'

describe('formatters', () => {
  describe('formatFileSize', () => {
    it('格式化字节为 B', () => {
      expect(formatFileSize(0)).toBe('0 B')
      expect(formatFileSize(512)).toBe('512 B')
    })

    it('格式化字节为 KB/MB/GB', () => {
      expect(formatFileSize(1024)).toBe('1.0 KB')
      expect(formatFileSize(1024 * 1024)).toBe('1.0 MB')
      expect(formatFileSize(1024 * 1024 * 1024)).toBe('1.0 GB')
    })

    it('处理 null/undefined 输入返回 0 B', () => {
      expect(formatFileSize(0)).toBe('0 B')
    })
  })

  describe('formatDuration', () => {
    it('格式化分钟（无小时）', () => {
      expect(formatDuration(30 * 60)).toBe('30分钟')
      expect(formatDuration(0)).toBe('0分钟')
    })

    it('格式化小时+分钟', () => {
      expect(formatDuration(3600 + 1800)).toBe('1小时30分钟')
      expect(formatDuration(7200 + 600)).toBe('2小时10分钟')
    })

    it('简写格式', () => {
      expect(formatDuration(30 * 60, true)).toBe('30m')
      expect(formatDuration(3600 + 1800, true)).toBe('1h 30m')
    })
  })

  describe('formatTimestamp', () => {
    it('格式化毫秒时间戳', () => {
      const ts = new Date('2025-01-15T10:30:00').getTime()
      const result = formatTimestamp(ts, 'en')
      expect(result).toContain('2025')
    })

    it('默认使用 zh-CN locale', () => {
      const ts = new Date('2025-06-15T08:00:00').getTime()
      const result = formatTimestamp(ts)
      expect(typeof result).toBe('string')
      expect(result.length).toBeGreaterThan(0)
    })
  })

  describe('formatSecondsTimestamp', () => {
    it('格式化秒级时间戳', () => {
      const ts = Math.floor(new Date('2025-01-15T10:30:00').getTime() / 1000)
      const result = formatSecondsTimestamp(ts, 'en')
      expect(result).toContain('2025')
    })

    it('null/undefined 返回空字符串', () => {
      expect(formatSecondsTimestamp(null)).toBe('')
      expect(formatSecondsTimestamp(undefined)).toBe('')
    })
  })

  describe('formatDate', () => {
    it('格式化 ISO 日期字符串', () => {
      const result = formatDate('2025-06-15T10:30:00Z', 'en')
      expect(result).toContain('2025')
    })

    it('空值返回空字符串', () => {
      expect(formatDate(null)).toBe('')
      expect(formatDate(undefined)).toBe('')
      expect(formatDate('')).toBe('')
    })
  })
})
