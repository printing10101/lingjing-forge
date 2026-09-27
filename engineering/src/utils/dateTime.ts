import { formatDate } from './formatters'

/**
 * 格式化 ISO 日期字符串为本地化时间（zh-CN），
 * null/无效值返回 '—'。
 */
export function formatDateTime(iso: string | null): string {
  if (!iso) return '\u2014'
  const result = formatDate(iso)
  return result || iso
}
