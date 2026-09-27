/** 通用 API 响应解包工具。

消除 6 个 Store 中各自重复的 ``unwrap()`` 函数定义。
处理 ``{data: {data: T}}`` 的双层嵌套响应格式。
*/

/**
 * 从标准 API 响应中提取实际数据 payload。
 *
 * 兼容两种响应格式：
 * - ``{ data: { data: T } }`` — 双层嵌套
 * - ``{ data: T }``            — 单层嵌套
 * - ``T``                       — 直接返回（兼容旧版）
 */
export function unwrap<T>(response: unknown): T {
  const r = response as { data?: { data?: T } | T }
  if (r && typeof r === 'object' && 'data' in r) {
    const body = r.data as { data?: T } | T
    if (body && typeof body === 'object' && 'data' in body) {
      return (body as { data?: T }).data as T
    }
    return body as T
  }
  return response as T
}
