/**
 * P3-1 前端自主化：Element Plus Headless 包装层 —— 通用数据表格逻辑。
 *
 * 将 Element Plus `el-table` + `el-pagination` 常用的「分页 / 排序 /
 * 多选 / 加载状态」逻辑抽为纯逻辑 composable，组件层只做绑定，实现：
 *   - 与 UI 框架解耦（不 import element-plus，便于独立测试与换肤）
 *   - 边界处理（页码越界钳制、排序切换方向轮转、选择去重）
 *   - 幂等（重复设置相同分页不触发 reload）
 *
 * 用法：
 *   const table = useDataTable({ pageSize: 20, fetcher: loadPage })
 *   table.setPage(2)           // 触发 fetcher({ page: 2, size: 20 })
 *   table.sortBy('time')       // asc → desc → none 轮转
 *   table.toggleRow(id)        // 多选去重
 *
 * 纯逻辑：可单测覆盖（不依赖 Vue 组件挂载）。
 */

import { computed, ref, watch, type Ref } from 'vue'

export type SortDirection = 'asc' | 'desc' | null

export interface SortState {
  prop: string
  direction: SortDirection
}

export interface PageQuery {
  page: number
  size: number
  sort?: SortState
}

export interface PageResult<T> {
  items: T[]
  total: number
}

export interface DataTableOptions<T, Q extends Record<string, unknown>> {
  /** 默认每页条数 */
  pageSize?: number
  /** 默认排序 */
  defaultSort?: SortState
  /** 数据获取函数（返回 { items, total }） */
  fetcher: (query: PageQuery & Q) => Promise<PageResult<T>>
  /** 行唯一键提取（多选去重用） */
  rowKey?: (row: T) => string | number
}

const DIRECTION_CYCLE: SortDirection[] = ['asc', 'desc', null]

/** 页码钳制：非法值回退到 1，超上限取 lastPage */
export function clampPage(page: number, total: number, size: number): number {
  if (!Number.isFinite(page) || page < 1) return 1
  const last = Math.max(1, Math.ceil(total / Math.max(1, size)))
  return Math.min(page, last)
}

/** 排序方向轮转：asc → desc → none → asc */
export function nextDirection(current: SortDirection): SortDirection {
  const idx = DIRECTION_CYCLE.indexOf(current)
  return DIRECTION_CYCLE[(idx + 1) % DIRECTION_CYCLE.length]
}

/**
 * 通用数据表格 Headless composable。
 */
export function useDataTable<T, Q extends Record<string, unknown> = Record<string, unknown>>(
  options: DataTableOptions<T, Q>,
) {
  const page = ref(1)
  const size = ref(options.pageSize ?? 20)
  const sort = ref<SortState | null>(options.defaultSort ?? null)
  const items = ref<T[]>([]) as Ref<T[]>
  const total = ref(0)
  const loading = ref(false)
  const errorMessage = ref('')
  const selectedKeys = ref<Array<string | number>>([])

  // 与 fetcher 相同签名（供组件透传额外查询参数）
  let extraQuery: Q = {} as Q

  /** 是否第一页（供 el-pagination 禁用上一页） */
  const isFirstPage = computed(() => page.value <= 1)
  /** 是否最后一页 */
  const isLastPage = computed(
    () => page.value >= Math.max(1, Math.ceil(total.value / Math.max(1, size.value))),
  )

  /** 当前选中行集合（去重后） */
  const selectedRows = computed<T[]>(() => {
    const keyFn = options.rowKey
    if (!keyFn) return []
    return items.value.filter((row) => selectedKeys.value.includes(keyFn(row)))
  })

  /** 是否全部选中（当前页） */
  const isAllSelected = computed(
    () =>
      items.value.length > 0 &&
      items.value.every((row) => {
        const keyFn = options.rowKey
        return keyFn ? selectedKeys.value.includes(keyFn(row)) : false
      }),
  )

  async function reload(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
      const query = {
        page: page.value,
        size: size.value,
        ...(sort.value ? { sort: sort.value } : {}),
        ...extraQuery,
      } as PageQuery & Q
      const result = await options.fetcher(query)
      items.value = result.items
      total.value = result.total
      // 页码越界钳制（如删除末页最后一行后页码回退）
      const clamped = clampPage(page.value, result.total, size.value)
      if (clamped !== page.value) {
        page.value = clamped
        await reload()
      }
    } catch (e) {
      errorMessage.value = e instanceof Error ? e.message : String(e)
      items.value = []
    } finally {
      loading.value = false
    }
  }

  /** 设置页码（相同页码不触发 reload——幂等） */
  function setPage(p: number): void {
    const next = clampPage(p, total.value, size.value)
    if (next !== page.value) {
      page.value = next
      void reload()
    }
  }

  /** 设置每页条数（重置到第 1 页） */
  function setPageSize(s: number): void {
    if (s !== size.value) {
      size.value = s
      page.value = 1
      void reload()
    }
  }

  /** 按字段排序（方向轮转；null 表示取消排序） */
  function sortBy(prop: string): void {
    if (sort.value?.prop === prop) {
      const next = nextDirection(sort.value.direction)
      sort.value = next ? { prop, direction: next } : null
    } else {
      sort.value = { prop, direction: 'asc' }
    }
    page.value = 1
    void reload()
  }

  /** 切换行选中（去重） */
  function toggleRow(row: T): void {
    const keyFn = options.rowKey
    if (!keyFn) return
    const key = keyFn(row)
    const idx = selectedKeys.value.indexOf(key)
    if (idx >= 0) {
      selectedKeys.value.splice(idx, 1)
    } else {
      selectedKeys.value.push(key)
    }
  }

  /** 全选/取消全选（当前页） */
  function toggleSelectAll(): void {
    if (isAllSelected.value) {
      const keyFn = options.rowKey
      if (!keyFn) return
      const keys = items.value.map(keyFn)
      selectedKeys.value = selectedKeys.value.filter((k) => !keys.includes(k))
    } else {
      const keyFn = options.rowKey
      if (!keyFn) return
      const keys = items.value.map(keyFn)
      for (const k of keys) {
        if (!selectedKeys.value.includes(k)) selectedKeys.value.push(k)
      }
    }
  }

  /** 清空选择 */
  function clearSelection(): void {
    selectedKeys.value = []
  }

  /** 更新额外查询参数（透传给 fetcher）并重载 */
  function setExtraQuery(q: Q): void {
    extraQuery = q
    page.value = 1
    void reload()
  }

  // 外部监听 size/sort 变化自动重载（如重置筛选时）
  watch([size, sort], () => {
    page.value = 1
    void reload()
  })

  return {
    page,
    size,
    sort,
    items,
    total,
    loading,
    errorMessage,
    selectedKeys,
    isFirstPage,
    isLastPage,
    isAllSelected,
    selectedRows,
    reload,
    setPage,
    setPageSize,
    sortBy,
    toggleRow,
    toggleSelectAll,
    clearSelection,
    setExtraQuery,
  }
}

export type DataTable<T, Q extends Record<string, unknown> = Record<string, unknown>> =
  ReturnType<typeof useDataTable<T, Q>>
