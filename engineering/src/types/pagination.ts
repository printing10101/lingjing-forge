/** 共享分页状态类型。

消除 6 个 Store 中各自独立定义的 ``PaginationState`` 接口重复。
*/

/** 标准分页状态 */
export interface PaginationState {
  total: number
  limit: number
  offset: number
}

/** 创建默认分页状态的工厂函数 */
export function createPagination(
  limit: number = 20,
  offset: number = 0,
): PaginationState {
  return { total: 0, limit, offset }
}

/** 根据总数计算下一页 offset */
export function nextOffset(state: PaginationState): number {
  return state.offset + state.limit
}

/** 是否还有更多页 */
export function hasMore(state: PaginationState): boolean {
  return state.offset + state.limit < state.total
}
